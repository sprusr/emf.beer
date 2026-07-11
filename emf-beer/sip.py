import asyncio
import logging
import tempfile
from typing import Callable, Coroutine

import pjsua2 as pj
import soundfile as sf
from pocket_tts import TTSModel

from .settings import settings

tts_model = TTSModel.load_model()
voice_state = tts_model.get_state_for_audio_prompt("alba")

logger = logging.getLogger(__name__)

class Endpoint(pj.Endpoint):
    def __init__(self):
        super().__init__()
        logger.warning("Creating lib!")
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

        asyncio.create_task(self._loop())

    def destroy(self):
        self.libDestroy()

    async def _loop(self):
        while True:
            result = self.libHandleEvents(0)
            if result < 0:
                logger.warning("libHandleEvents error: %s", -result)
            await asyncio.sleep(0.05)


class Account(pj.Account):
    handler: Callable[[Call], Coroutine[None, None, None]]

    calls: list[_Call] = []

    def __init__(self, handler: Callable[[Call], Coroutine[None, None, None]]):
        super().__init__()
        self.handler = handler

        config = pj.AccountConfig()
        config.idUri = f"sip:{settings.sip_username}@sip.emf.camp"
        config.regConfig.registrarUri = "sip:sip.emf.camp"

        creds = pj.AuthCredInfo(
            "digest", "*", settings.sip_username, 0, settings.sip_password
        )
        config.sipConfig.authCreds.append(creds)

        config.callConfig.timerUse = pj.PJSUA_SIP_TIMER_OPTIONAL
        config.callConfig.timerMinSESec = 90
        config.callConfig.timerSessExpiresSec = 1800

        config.mediaConfig.transportConfig.boundAddress = settings.udp_bind_address
        config.mediaConfig.transportConfig.port = 4000
        config.mediaConfig.transportConfig.portRange = 4
        config.mediaConfig.transportConfig.randomizePort = False
        config.mediaConfig.noVad = True
        config.mediaConfig.ecTailLen = 0

        if settings.public_ipv4:
            config.mediaConfig.transportConfig.publicAddress = settings.public_ipv4
            # config.natConfig.iceEnabled = True
            # config.natConfig.iceManualHost.clear()
            # config.natConfig.iceManualHost.push_back(settings.public_ipv4)

        self.create(config, True)

    def onIncomingCall(self, prm: pj.OnIncomingCallParam):
        call = _Call(self, self.handler, prm.callId)
        self.calls.append(call)
        op = pj.CallOpParam()
        op.statusCode = pj.PJSIP_SC_ACCEPTED
        call.answer(op)


class Phone:
    _account: Account

    def __init__(self, account: Account):
        self._account = account

    async def call(
        self, to: int, handler: Callable[[Call], Coroutine[None, None, None]]
    ):
        call = _Call(self._account, handler)
        self._account.calls.append(call)
        logger.warning("Making call to %s", f"sip:{to}@sip.emf.camp")
        await asyncio.sleep(0.5)
        call.makeCall(f"sip:{to}@sip.emf.camp;transport=udp", pj.CallOpParam(True))


class _Call(pj.Call):
    account: Account
    handler: Callable[[Call], Coroutine[None, None, None]]

    def __init__(
        self,
        acc: Account,
        handler: Callable[[Call], Coroutine[None, None, None]],
        call_id: int = pj.PJSUA_INVALID_ID,
    ):
        super().__init__(acc, call_id)
        self.account = acc
        self.handler = handler

    def onCallState(self, prm):
        info: pj.CallInfo = self.getInfo()
        logger.warning("Call state change: %s", info.state)
        if info.state is pj.PJSIP_INV_STATE_CONFIRMED:
            asyncio.create_task(self._handle_call(self.handler))
        if info.state is pj.PJSIP_INV_STATE_DISCONNECTED:
            self.account.calls.remove(self)

    def onStreamCreated(self, prm: pj.OnStreamCreatedParam):
        logger.warning("Stream created: %s", prm)

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


class Call:
    _call: _Call

    def __init__(self, call: _Call):
        self._call = call

    async def say(self, text: str) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav")
        audio = tts_model.generate_audio(voice_state, text)
        sf.write(tmp.name, audio.numpy(), tts_model.sample_rate, subtype="PCM_16")

        media = self._call.getAudioMedia(-1)
        player = AudioMediaPlayer()
        player.createPlayer(tmp.name, pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(media)
        await player.done
        player.stopTransmit(media)

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

    async def hangup(self) -> None:
        op = pj.CallOpParam()
        op.statusCode = pj.PJSIP_SC_OK
        self._call.hangup(op)
