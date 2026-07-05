import asyncio
import dataclasses

from voip.ai import TTSMixin
from voip.audio import AudioCall
from voip.sip import Dialog, SessionInitiationProtocol, SipURI


@dataclasses.dataclass(kw_only=True, slots=True)
class TTSSession(TTSMixin, AudioCall):
    text: str = ""
    done: asyncio.Future[None] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        asyncio.create_task(self.send_speech(self.text))

    def on_audio_sent(self) -> None:
        asyncio.create_task(self.hang_up())

    async def hang_up(self) -> None:
        try:
            await AudioCall.hang_up(self)
        finally:
            if self.done is not None and not self.done.done():
                self.done.set_result(None)


@dataclasses.dataclass(kw_only=True, slots=True)
class BeerAlertDialog(Dialog):
    def call_received(self) -> None:
        self.ringing()
        self.answer(session_class=TTSSession)


async def connect(username: str, password: str) -> SessionInitiationProtocol:
    aor = SipURI.parse(f"sip:{username}:{password}@sip.emf.camp;transport=UDP")
    sip = await SessionInitiationProtocol.run(aor, BeerAlertDialog)
    return sip


async def call(sip: SessionInitiationProtocol, to: str, text: str) -> None:
    dialog = sip.dialog_class(sip=sip)
    done = asyncio.get_running_loop().create_future()
    await dialog.dial(
        SipURI.parse(f"sip:{to}@sip.emf.camp"),
        session_class=TTSSession,
        text=text,
        done=done,
    )
    await done
