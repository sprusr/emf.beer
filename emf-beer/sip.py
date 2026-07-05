import asyncio
import dataclasses
from typing import Callable, Coroutine

import librosa
from voip.ai import TTSMixin
from voip.audio import AudioCall
from voip.sip import Dialog, SessionInitiationProtocol, SipURI


class Call:
    _session: CallSession

    def __init__(self, session: CallSession) -> None:
        self._session = session

    async def say(self, text: str) -> None:
        if self._session.audio_done is not None:
            return
        self._session.audio_done = asyncio.get_running_loop().create_future()
        await self._session.send_speech(text)
        await self._session.audio_done
        self._session.audio_done = None

    async def play(self, file: str) -> None:
        if self._session.audio_done is not None:
            return
        self._session.audio_done = asyncio.get_running_loop().create_future()
        audio, _ = librosa.load(file, sr=self._session.codec.sample_rate_hz, mono=True)
        await self._session.send_audio(audio)
        await self._session.audio_done
        self._session.audio_done = None

    async def transfer(self, to: str) -> None:
        # TODO: implement
        pass

    async def hangup(self) -> None:
        await self._session.hang_up()


@dataclasses.dataclass(kw_only=True, slots=True)
class CallSession(TTSMixin, AudioCall):
    handler: Callable[[Call], Coroutine[None, None, None]]
    done: asyncio.Future[None] | None = None
    audio_done: asyncio.Future[None] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        asyncio.create_task(self.handle_call())

    def on_audio_sent(self) -> None:
        if self.audio_done is not None:
            self.audio_done.set_result(None)

    async def handle_call(self) -> None:
        await self.handler(Call(self))
        if self.done is not None and not self.done.done():
            await self.hang_up()

    async def hang_up(self) -> None:
        try:
            await AudioCall.hang_up(self)
        finally:
            if self.done is not None and not self.done.done():
                self.done.set_result(None)


async def connect(
    username: str,
    password: str,
    handler: Callable[[Call], Coroutine[None, None, None]],
) -> SessionInitiationProtocol:
    aor = SipURI.parse(f"sip:{username}:{password}@sip.emf.camp;transport=UDP")

    @dataclasses.dataclass(kw_only=True, slots=True)
    class HandlerDialog(Dialog):
        def call_received(self) -> None:
            self.ringing()
            self.answer(session_class=CallSession, handler=handler)

    sip = await SessionInitiationProtocol.run(aor, HandlerDialog)
    return sip


async def call(
    sip: SessionInitiationProtocol,
    to: str,
    handler: Callable[[Call], Coroutine[None, None, None]],
) -> None:
    dialog = sip.dialog_class(sip=sip)
    done = asyncio.get_running_loop().create_future()
    await dialog.dial(
        SipURI.parse(f"sip:{to}@sip.emf.camp"),
        session_class=CallSession,
        handler=handler,
        done=done,
    )
    await done
