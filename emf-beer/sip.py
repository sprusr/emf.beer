import asyncio
import socket
import tempfile
import threading
from typing import Callable, Coroutine

import numpy as np
import pjsua2 as pj
import soundfile as sf
from pocket_tts import TTSModel

from .settings import settings

tts_model = TTSModel.load_model()
voice_state = tts_model.get_state_for_audio_prompt("alba")

# The TTS model is not thread-safe, so serialise access to it. Generation runs
# in a worker thread (see Call.say) to keep the event loop responsive; this lock
# ensures only one generation touches the shared model at a time.
_tts_lock = threading.Lock()


def _chunk_to_pcm16(chunk) -> bytes:
    """Convert a float32 TTS audio chunk into host-order 16-bit PCM bytes."""
    samples = np.clip(chunk.numpy(), -1.0, 1.0)
    return (samples * 32767.0).astype(np.int16).tobytes()


def _resolve_ips(host: str) -> set[str]:
    """Resolve a hostname to the set of IP addresses it points at."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return set()
    return {str(info[4][0]) for info in infos}


def _source_ip(src_address: str) -> str:
    """Extract the IP from a pjsua2 srcAddress like "1.2.3.4:5060" or "[::1]:5060"."""
    return src_address.rsplit(":", 1)[0].strip("[]")


class Endpoint(pj.Endpoint):
    def __init__(self):
        super().__init__()
        self.libCreate()

        config = pj.EpConfig()
        config.uaConfig.threadCnt = 0
        config.uaConfig.mainThreadOnly = True

        self.libInit(config)

        transport_config = pj.TransportConfig()
        transport_config.port = 5080
        transport_config.boundAddress = settings.udp_bind_address
        if settings.public_ipv4:
            transport_config.publicAddress = settings.public_ipv4

        self.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport_config)

        self.libStart()
        self.audDevManager().setNullDev()

        for codec in self.codecEnum2():
            keep = codec.codecId.startswith(("PCMU/", "PCMA/"))
            self.codecSetPriority(codec.codecId, 255 if keep else 0)

        asyncio.create_task(self._loop())

    def destroy(self):
        self.libDestroy()

    async def _loop(self):
        while True:
            self.libHandleEvents(0)
            await asyncio.sleep(0.05)


class Account(pj.Account):
    handler: Callable[[Call], Coroutine[None, None, None]]
    calls: list[_Call] = []
    max_calls: int
    allowed_source_ips: set[str]
    _slot_freed: asyncio.Event

    def __init__(
        self,
        handler: Callable[[Call], Coroutine[None, None, None]],
        max_calls: int = 3,
    ):
        super().__init__()
        self.handler = handler
        self.max_calls = max_calls
        self.allowed_source_ips = _resolve_ips(settings.sip_server)

        self._slot_freed = asyncio.Event()

        config = pj.AccountConfig()
        config.idUri = f"sip:{settings.sip_username}@{settings.sip_server}"

        config.regConfig.registrarUri = f"sip:{settings.sip_server}"

        config.sipConfig.authCreds.append(
            pj.AuthCredInfo(
                "digest", "*", settings.sip_username, 0, settings.sip_password
            )
        )

        config.callConfig.timerUse = pj.PJSUA_SIP_TIMER_OPTIONAL
        config.callConfig.timerMinSESec = 90
        config.callConfig.timerSessExpiresSec = 1800

        config.mediaConfig.transportConfig.boundAddress = settings.udp_bind_address
        config.mediaConfig.transportConfig.port = 4000
        config.mediaConfig.transportConfig.portRange = max_calls * 2
        config.mediaConfig.transportConfig.randomizePort = False
        config.mediaConfig.noVad = True
        config.mediaConfig.ecTailLen = 0
        if settings.public_ipv4:
            config.mediaConfig.transportConfig.publicAddress = settings.public_ipv4

        self.create(config, True)

    async def wait_for_slot(self) -> None:
        while len(self.calls) >= self.max_calls:
            self._slot_freed.clear()
            await self._slot_freed.wait()

    def slot_freed(self) -> None:
        self._slot_freed.set()

    def onIncomingCall(self, prm: pj.OnIncomingCallParam):
        call = _Call(self, self.handler, prm.callId)
        op = pj.CallOpParam()

        src_ip = _source_ip(prm.rdata.srcAddress)
        if src_ip not in self.allowed_source_ips:
            op.statusCode = pj.PJSIP_SC_FORBIDDEN
            call.answer(op)
            return

        if len(self.calls) >= self.max_calls:
            op.statusCode = pj.PJSIP_SC_BUSY_HERE
        else:
            self.calls.append(call)
            op.statusCode = pj.PJSIP_SC_ACCEPTED
        call.answer(op)


class Phone:
    _account: Account

    def __init__(self, account: Account):
        self._account = account

    async def call(
        self, to: int, handler: Callable[[Call], Coroutine[None, None, None]]
    ):
        await self._account.wait_for_slot()
        call = _Call(self._account, handler)
        self._account.calls.append(call)
        try:
            await asyncio.sleep(0.5)
            call.makeCall(f"sip:{to}@sip.emf.camp", pj.CallOpParam(True))
            await call.done
        finally:
            if call in self._account.calls:
                self._account.calls.remove(call)
                self._account.slot_freed()

    async def tts(self, text: str) -> tempfile._TemporaryFileWrapper[bytes]:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav")
        audio = tts_model.generate_audio(voice_state, text)
        sf.write(tmp.name, audio.numpy(), tts_model.sample_rate, subtype="PCM_16")
        return tmp


class _Call(pj.Call):
    account: Account
    handler: Callable[[Call], Coroutine[None, None, None]]
    done: asyncio.Future[None]
    transferred: asyncio.Future[None]

    def __init__(
        self,
        acc: Account,
        handler: Callable[[Call], Coroutine[None, None, None]],
        call_id: int = pj.PJSUA_INVALID_ID,
    ):
        super().__init__(acc, call_id)
        self.account = acc
        self.handler = handler
        self.done = asyncio.Future()
        self.transferred = asyncio.Future()

    def onCallState(self, prm):
        info: pj.CallInfo = self.getInfo()
        if info.state is pj.PJSIP_INV_STATE_CONFIRMED:
            asyncio.create_task(self._handle_call(self.handler))
        if info.state is pj.PJSIP_INV_STATE_DISCONNECTED:
            if not self.done.done():
                self.done.set_result(None)
            if self in self.account.calls:
                self.account.calls.remove(self)
                self.account.slot_freed()

    def onCallTransferStatus(self, prm: pj.OnCallTransferStatusParam):
        self.transferred.set_result(None)

    async def _handle_call(
        self, handler: Callable[[Call], Coroutine[None, None, None]]
    ) -> None:
        await asyncio.sleep(0.5)
        handler_call = Call(self)
        await handler(handler_call)
        await asyncio.sleep(0.5)
        op = pj.CallOpParam()
        op.statusCode = pj.PJSIP_SC_OK
        self.hangup(op)


class AudioMediaPlayer(pj.AudioMediaPlayer):
    done: asyncio.Future

    def __init__(self):
        super().__init__()
        self.done = asyncio.Future()

    def onEof2(self):
        self.done.set_result(None)


class StreamPlayer(pj.AudioMediaPort):
    """An audio media port fed with PCM16 chunks as they are produced.

    Audio is pushed into a buffer via feed() (typically from a TTS generation
    worker thread) while pjmedia's clock thread pulls fixed-size frames out of
    it in onFrameRequested(). This lets us start transmitting to the call as
    soon as the first chunk is ready, rather than waiting for the whole
    utterance to be generated and written to a file.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._finished = False
        self._done_set = False
        self.done: asyncio.Future[None] = loop.create_future()

    def feed(self, pcm: bytes) -> None:
        """Append generated PCM16 audio to the buffer. Thread-safe."""
        with self._lock:
            self._buf.extend(pcm)

    def finish(self) -> None:
        """Signal that no more audio will be fed; done resolves once drained."""
        with self._lock:
            self._finished = True

    def _signal_done(self) -> None:
        # Runs on pjmedia's clock thread, so hop back to the loop thread to
        # resolve the future safely.
        if not self._done_set:
            self._done_set = True
            self._loop.call_soon_threadsafe(
                lambda: self.done.done() or self.done.set_result(None)
            )

    def onFrameRequested(self, frame: pj.MediaFrame) -> None:
        capacity = frame.size
        with self._lock:
            take = min(capacity, len(self._buf))
            chunk = bytes(self._buf[:take])
            del self._buf[:take]
            drained = self._finished and not self._buf

        # Zero-pad to a full frame: leading/underrun silence while we wait for
        # more audio, and the short final frame once generation is done.
        if take < capacity:
            chunk += bytes(capacity - take)

        frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
        frame.buf = pj.ByteVector(chunk)

        if drained:
            self._signal_done()


class Call:
    _call: _Call

    def __init__(self, call: _Call):
        self._call = call

    async def say(self, text: str) -> None:
        loop = asyncio.get_running_loop()
        player = StreamPlayer(loop)

        fmt = pj.MediaFormatAudio()
        fmt.init(pj.PJMEDIA_FORMAT_PCM, tts_model.sample_rate, 1, 20000, 16)
        player.createPort("tts", fmt)

        media = self._call.getAudioMedia(-1)
        player.startTransmit(media)
        try:
            # Generate off the event loop; chunks stream into the player as they
            # become available and are transmitted by pjmedia's clock thread.
            await asyncio.to_thread(self._generate, player, text)
            player.finish()
            await player.done
        finally:
            player.stopTransmit(media)

    @staticmethod
    def _generate(player: StreamPlayer, text: str) -> None:
        with _tts_lock:
            for chunk in tts_model.generate_audio_stream(voice_state, text):
                player.feed(_chunk_to_pcm16(chunk))

    async def play(self, file: str) -> None:
        media = self._call.getAudioMedia(-1)
        player = AudioMediaPlayer()
        player.createPlayer(file, pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(media)
        await player.done
        player.stopTransmit(media)

    async def pause(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def transfer(self, to: int) -> None:
        op = pj.CallOpParam()
        op.statusCode = pj.PJSIP_SC_OK
        self._call.xfer(f"sip:{to}@sip.emf.camp", op)
        await self._call.transferred

    async def hangup(self) -> None:
        op = pj.CallOpParam()
        op.statusCode = pj.PJSIP_SC_OK
        self._call.hangup(op)
