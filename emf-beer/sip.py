import asyncio
import tempfile
from typing import Callable, Coroutine

import librosa
import pjsua2 as pj
import soundfile as sf
from pocket_tts import TTSModel

from .settings import settings

tts_model = TTSModel.load_model()
voice_state = tts_model.get_state_for_audio_prompt("alba")


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
        self.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport_config)

        self.libStart()
        self.audDevManager().setNullDev()

        asyncio.create_task(self._onTimer())

    def destroy(self):
        self.libDestroy()

    async def _onTimer(self):
        self.libHandleEvents(10)
        await asyncio.sleep(0.05)
        asyncio.create_task(self._onTimer())


class Account(pj.Account):
    handler: Callable[[HandlerCall], Coroutine[None, None, None]]

    calls: list[Call] = []

    def __init__(self, handler: Callable[[HandlerCall], Coroutine[None, None, None]]):
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

        self.create(config, True)

    def onIncomingCall(self, prm: pj.OnIncomingCallParam):
        call = Call(self, self.handler, prm.callId)
        self.calls.append(call)
        op = pj.CallOpParam()
        op.statusCode = pj.PJSIP_SC_ACCEPTED
        call.answer(op)

    async def call(
        self, to: int, handler: Callable[[HandlerCall], Coroutine[None, None, None]]
    ):
        call = Call(self, handler)
        self.calls.append(call)
        call.makeCall(f"sip:{to}@sip.emf.camp", pj.CallOpParam())


class Call(pj.Call):
    account: Account
    handler: Callable[[HandlerCall], Coroutine[None, None, None]]

    def __init__(
        self,
        acc: Account,
        handler: Callable[[HandlerCall], Coroutine[None, None, None]],
        call_id: int = pj.PJSUA_INVALID_ID,
    ):
        super().__init__(acc, call_id)
        self.account = acc
        self.handler = handler

    def onCallState(self, prm):
        info: pj.CallInfo = self.getInfo()
        if info.state == pj.PJSIP_INV_STATE_CONFIRMED:
            asyncio.create_task(self._handle_call(self.handler))
        if info.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.account.calls.remove(self)

    async def _handle_call(
        self, handler: Callable[[HandlerCall], Coroutine[None, None, None]]
    ) -> None:
        await asyncio.sleep(1)
        handler_call = HandlerCall(self)
        await handler(handler_call)
        self.hangup_ok()

    async def play(self, file_name: str):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav")
        audio, samplerate = librosa.load(file_name, mono=True)
        sf.write(tmp.name, audio, int(samplerate), subtype="PCM_16")

        media = self.getAudioMedia(-1)
        player = AudioMediaPlayer()
        player.createPlayer(tmp.name, pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(media)
        await player.done
        player.stopTransmit(media)

    async def say(self, text: str):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav")
        audio = tts_model.generate_audio(voice_state, text)
        sf.write(tmp.name, audio.numpy(), tts_model.sample_rate, subtype="PCM_16")

        media = self.getAudioMedia(-1)
        player = AudioMediaPlayer()
        player.createPlayer(tmp.name, pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(media)
        await player.done
        player.stopTransmit(media)

    def hangup_ok(self):
        op = pj.CallOpParam()
        op.statusCode = pj.PJSIP_SC_OK
        self.hangup(op)

    def transfer(self, to: int) -> None:
        op = pj.CallOpParam()
        op.statusCode = pj.PJSIP_SC_OK
        self.xfer(f"sip:{to}@sip.emf.camp", op)


class AudioMediaPlayer(pj.AudioMediaPlayer):
    done: asyncio.Future

    def __init__(self):
        super().__init__()
        self.done = asyncio.Future()

    def onEof2(self):
        self.done.set_result(None)


class HandlerCall:
    _call: Call

    def __init__(self, call: Call):
        self._call = call

    async def say(self, text: str) -> None:
        await self._call.say(text)

    async def play(self, file: str) -> None:
        await self._call.play(file)

    async def pause(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def transfer(self, to: int) -> None:
        self._call.transfer(to)

    async def hangup(self) -> None:
        self._call.hangup_ok()
