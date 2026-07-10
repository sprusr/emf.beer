import asyncio
import io
import threading
from os import PathLike
from typing import BinaryIO

import librosa
import numpy as np
import pjsua2 as pj


class LibrosaAudioPlayer(pj.AudioMediaPort):
    def __init__(
        self,
        source: str | PathLike | bytes | bytearray | memoryview | BinaryIO,
        destination: pj.AudioMedia,
        *,
        sample_rate: int = 24_000,
        ptime_ms: int = 20,
        prebuffer_ms: int = 100,
        max_buffer_ms: int = 2_000,
        res_type: str = "soxr_hq",
    ):
        super().__init__()

        self.source = source
        self.destination = destination
        self.sample_rate = sample_rate
        self.ptime_ms = ptime_ms
        self.prebuffer_ms = prebuffer_ms
        self.res_type = res_type

        self._loop = asyncio.get_running_loop()
        self._drained = asyncio.Event()

        self._condition = threading.Condition()
        self._buffer = bytearray()

        max_bytes = sample_rate * 2 * max_buffer_ms // 1000
        self._max_bytes = max(2, max_bytes - max_bytes % 2)

        self._eof = False
        self._stopped = False
        self._connected = False
        self._producer_error = None

        self._task = self._loop.create_task(self._run())

    async def _run(self):
        try:
            samples = await asyncio.to_thread(self._load_audio)

            if self._stopped:
                return

            fmt = pj.MediaFormatAudio()
            fmt.init(
                pj.PJMEDIA_FORMAT_PCM,
                self.sample_rate,
                1,
                self.ptime_ms * 1000,
                16,
            )

            self.createPort("librosa-player", fmt)

            prebuffer_samples = min(
                len(samples),
                self.sample_rate * self.prebuffer_ms // 1000,
            )

            if prebuffer_samples:
                pcm = self._samples_to_pcm16(samples[:prebuffer_samples])
                self._buffer.extend(pcm)

            self.startTransmit(self.destination)
            self._connected = True

            await asyncio.to_thread(
                self._feed_samples,
                samples,
                prebuffer_samples,
            )

            await self._drained.wait()

            if self._producer_error is not None:
                raise self._producer_error

        finally:
            if self._connected:
                try:
                    self.stopTransmit(self.destination)
                except pj.Error:
                    pass

                self._connected = False

    def _load_audio(self) -> np.ndarray:
        source = self.source

        if isinstance(source, (bytes, bytearray, memoryview)):
            source = io.BytesIO(bytes(source))

        samples, actual_rate = librosa.load(
            source,
            sr=self.sample_rate,
            mono=True,
            dtype=np.float32,
            res_type=self.res_type,
        )

        if actual_rate != self.sample_rate:
            raise RuntimeError(f"Expected {self.sample_rate} Hz, got {actual_rate} Hz")

        return np.ascontiguousarray(samples, dtype=np.float32)

    def _feed_samples(
        self,
        samples: np.ndarray,
        offset: int,
    ):
        block_samples = 4096

        try:
            while offset < len(samples) and not self._stopped:
                end = min(offset + block_samples, len(samples))

                pcm = self._samples_to_pcm16(samples[offset:end])

                if not self._write_pcm16(pcm):
                    break

                offset = end

        except Exception as exc:
            self._producer_error = exc

        finally:
            self._finish_input()

    @staticmethod
    def _samples_to_pcm16(samples: np.ndarray) -> bytes:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        samples = np.clip(samples, -1.0, 1.0)

        return np.rint(samples * 32767.0).astype("<i2").tobytes()

    def _write_pcm16(self, pcm: bytes) -> bool:
        data = memoryview(pcm).cast("B")
        offset = 0

        while offset < len(data):
            with self._condition:
                self._condition.wait_for(
                    lambda: self._stopped or len(self._buffer) < self._max_bytes
                )

                if self._stopped:
                    return False

                count = min(
                    len(data) - offset,
                    self._max_bytes - len(self._buffer),
                )

                self._buffer.extend(data[offset : offset + count])
                offset += count

        return True

    def _finish_input(self):
        with self._condition:
            self._eof = True

            if not self._buffer:
                self._signal_drained()

            self._condition.notify_all()

    def _signal_drained(self):
        self._loop.call_soon_threadsafe(self._drained.set)

    def onFrameRequested(self, frame):
        wanted = int(frame.size)

        with self._condition:
            count = min(wanted, len(self._buffer))

            pcm = bytes(self._buffer[:count])
            del self._buffer[:count]

            if count:
                self._condition.notify_all()

            if self._eof and not self._buffer:
                self._signal_drained()

        if len(pcm) < wanted:
            pcm += b"\x00" * (wanted - len(pcm))

        vector = pj.ByteVector()

        for byte in pcm:
            vector.append(byte)

        frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
        frame.buf = vector
        frame.size = len(pcm)

    async def wait(self):
        await self._task

    async def close(self):
        with self._condition:
            self._stopped = True
            self._eof = True
            self._buffer.clear()
            self._condition.notify_all()

        self._signal_drained()
        await self._task
