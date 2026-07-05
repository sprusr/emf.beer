import asyncio
import dataclasses
from typing import Any, Callable, Coroutine

import librosa
from voip.ai import TTSMixin
from voip.audio import AudioCall
from voip.sip import (
    Dialog,
    Request,
    Response,
    SessionInitiationProtocol,
    SIPMethod,
    SipURI,
)
from voip.sip.messages import SIPHeaderDict
from voip.sip.transactions import Transaction


class Call:
    session: CallSession

    def __init__(self, session: CallSession) -> None:
        self.session = session

    def _busy(self) -> bool:
        return self.session.audio_done is not None

    def _done(self) -> bool:
        return self.session.done is not None and self.session.done.done()

    async def say(self, text: str) -> None:
        if self._busy() or self._done():
            return
        self.session.audio_done = asyncio.get_running_loop().create_future()
        await self.session.send_speech(text)
        await self.session.audio_done
        self.session.audio_done = None

    async def play(self, file: str) -> None:
        if self._busy() or self._done():
            return
        self.session.audio_done = asyncio.get_running_loop().create_future()
        audio, _ = librosa.load(file, sr=self.session.codec.sample_rate_hz, mono=True)
        await self.session.send_audio(audio)
        await self.session.audio_done
        self.session.audio_done = None

    async def pause(self, seconds: float) -> None:
        if self._done():
            return
        await asyncio.sleep(seconds)

    async def transfer(self, to: str) -> None:
        if self._done():
            return
        await self.session.transfer(to)

    async def hangup(self) -> None:
        if self._done():
            return
        await self.session.hang_up()


@dataclasses.dataclass(kw_only=True, slots=True)
class ReferTransaction(Transaction):
    method: SIPMethod = SIPMethod.REFER

    @classmethod
    async def send(
        cls,
        *,
        sip: SessionInitiationProtocol,
        dialog: Dialog,
        refer_to: SipURI,
        **kwargs: Any,
    ) -> None:
        cseq = dialog.outbound_cseq
        dialog.outbound_cseq += 1
        tx = cls(sip=sip, dialog=dialog, cseq=cseq)
        request_uri = SipURI.parse(str(dialog.remote_contact).strip("<>").split(";")[0])
        headers = SIPHeaderDict(
            tx.headers
            | {
                "Max-Forwards": "70",
                "From": dialog.local_party,
                "To": dialog.remote_party,
                "Call-ID": dialog.call_id,
                "Contact": sip.contact,
                "Refer-To": f"<{refer_to}>",
                "Referred-By": f"<sip:{sip.aor.user}@{sip.aor.host}>",
                "Content-Length": "0",
            }
        )
        for route in dialog.route_set:
            headers.add("Route", route)
        tx.request = Request(method=SIPMethod.REFER, uri=request_uri, headers=headers)
        sip.register_transaction(tx)
        sip.send(tx.request)
        try:
            await tx
        except asyncio.CancelledError:
            sip.drop_transaction(tx)
            raise

    def response_received(self, response: Response) -> None:
        if response.status_code >= 200:
            self.sip.drop_transaction(self)
            self.complete()


@dataclasses.dataclass(kw_only=True, slots=True)
class CallSession(TTSMixin, AudioCall):
    handler: Callable[[Call], Coroutine[None, None, None]]
    done: asyncio.Future[None] | None = None
    audio_done: asyncio.Future[None] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        asyncio.create_task(self.handle_call())

    def _done(self) -> bool:
        return self.done is not None and self.done.done()

    def _set_done(self) -> None:
        if self.done is not None:
            self.done.set_result(None)
        else:
            self.done = asyncio.get_running_loop().create_future().set_result(None)

    def on_audio_sent(self) -> None:
        if self.audio_done is not None:
            self.audio_done.set_result(None)

    async def handle_call(self) -> None:
        await self.handler(Call(self))
        if not self._done():
            await self.hang_up()

    async def hang_up(self) -> None:
        try:
            await AudioCall.hang_up(self)
        finally:
            self._set_done()

    async def transfer(self, to: str) -> None:
        dialog = self.dialog
        if dialog is None or dialog.sip is None:
            return
        target = SipURI.parse(f"sip:{to}@{dialog.sip.aor.host}")
        await ReferTransaction.send(sip=dialog.sip, dialog=dialog, refer_to=target)
        self._set_done()


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
