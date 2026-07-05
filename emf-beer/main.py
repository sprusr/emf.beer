from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .settings import settings
from .sip import call, connect


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sip = await connect(
        username=settings.sip_username, password=settings.sip_password
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
        text="hhhh ...Important beer alert! There are new beers available!... hhhhh",
    )
    return "ok"
