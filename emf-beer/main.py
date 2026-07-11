from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .sip import Account, Call, Endpoint, Phone


async def incoming_handler(call: Call):
    await call.play("test.wav")
    await call.say("This is an incoming call!")
    await call.transfer(123)


async def outgoing_handler(session: Call):
    await session.play("test.wav")
    await session.say("This is an outgoing call!")
    await session.transfer(123)


@asynccontextmanager
async def lifespan(app: FastAPI):
    endpoint = Endpoint()
    account = Account(handler=incoming_handler)
    phone = Phone(account)

    app.state.account = account
    app.state.phone = phone

    yield

    endpoint.destroy()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def read_root():
    return f"{app.state.account.calls[0].getStreamStat(-1)}"


@app.get("/call/{to}")
async def read_item(request: Request, to: int):
    await request.app.state.phone.call(to, outgoing_handler)
    return "ok"
