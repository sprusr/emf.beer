from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .settings import settings
from .sip import Call, call, connect


async def incoming_handler(session: Call):
    await session.say("You have reached the beer hotline")


async def outgoing_handler(session: Call):
    await session.play("test.wav")
    await session.say(
        "hhhh ...Important beer alert! There are new beers available!... hhhhh"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sip = await connect(
        username=settings.sip_username,
        password=settings.sip_password,
        handler=incoming_handler,
    )
    await call(
        sip=app.state.sip,
        to="52881",
        handler=outgoing_handler,
    )
    yield
    app.state.sip.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/call/{to}")
async def read_item(request: Request, to: str):
    await call(
        sip=request.app.state.sip,
        to=to,
        handler=outgoing_handler,
    )
    return "ok"
