import asyncio
import threading

import numpy as np
import pjsua2 as pj
from pocket_tts import TTSModel


class PocketTTSPlayer(pj.AudioMediaPort):
    _models = {}
    _voice_states = {}

    _load_lock = threading.Lock()
    _generation_lock = threading.Lock()

    def __init__(
        self,
        text: str,
        destination: pj.AudioMedia,
        *,
        voice: str = "alba",
        language: str = "english",
        ptime_ms: int = 20,
        max_buffer_ms: int = 2000,
    ):
        super().__init__()

        self.text = text
        self.destination = destination
        self.voice = voice
        self.language = language
        self.ptime_ms = ptime_ms
        self.max_buffer_ms = max_buffer_ms

        self._loop = asyncio.get_running_loop()
        self._drained = asyncio.Event()

        self._condition = threading.Condition()
        self._buffer = bytearray()

        self._max_bytes = 0
        self._eof = False
        self._stopped = False
        self._connected = False
        self._producer_error = None

        self._task = self._loop.create_task(self._run())

    @classmethod
    def _load_engine(cls, language: str, voice: str):
        key = (language, voice)

        with cls._load_lock:
            model = cls._models.get(language)

            if model is None:
                model = TTSModel.load_model(language=language)
                cls._models[language] = model

            voice_state = cls._voice_states.get(key)

            if voice_state is None:
                voice_state = model.get_state_for_audio_prompt(voice)
                cls._voice_states[key] = voice_state

            return model, voice_state

    async def _run(self):
        try:
            model, voice_state = await asyncio.to_thread(
                self._load_engine,
                self.language,
                self.voice,
            )

            sample_rate = model.sample_rate

            max_bytes = sample_rate * 2 * self.max_buffer_ms // 1000
            self._max_bytes = max(2, max_bytes - max_bytes % 2)

            fmt = pj.MediaFormatAudio()
            fmt.init(
                pj.PJMEDIA_FORMAT_PCM,
                sample_rate,
                1,
                self.ptime_ms * 1000,
                16,
            )

            self.createPort("pocket-tts", fmt)
            self.startTransmit(self.destination)
            self._connected = True

            await asyncio.to_thread(
                self._generate,
                model,
                voice_state,
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

    def _generate(self, model, voice_state):
        try:
            with self._generation_lock:
                chunks = model.generate_audio_stream(
                    voice_state,
                    self.text,
                )

                for chunk in chunks:
                    if self._stopped:
                        break

                    samples = (
                        chunk.detach()
                        .cpu()
                        .numpy()
                        .astype(np.float32, copy=False)
                        .reshape(-1)
                    )

                    pcm = (
                        np.rint(np.clip(samples, -1.0, 1.0) * 32767.0)
                        .astype("<i2")
                        .tobytes()
                    )

                    if not self._write_pcm16(pcm):
                        break

        except Exception as exc:
            self._producer_error = exc

        finally:
            self._finish_input()

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
