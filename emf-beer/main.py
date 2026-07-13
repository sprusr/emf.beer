import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .bar import BarWatcher
from .sip import Account, Call, Endpoint, Phone


async def incoming_handler(call: Call):
    await call.say("The Robot Arms is currently closed!")
    await call.say("The Caught Try is currently closed!")


async def outgoing_handler(session: Call):
    await session.play("test.wav")
    await session.say("This is an outgoing call!")
    await session.transfer(123)


@asynccontextmanager
async def lifespan(app: FastAPI):
    endpoint = Endpoint()
    account = Account(handler=incoming_handler, max_calls=20)
    phone = Phone(account)
    watcher = BarWatcher(phone)
    watcher_task = asyncio.create_task(watcher.run())

    app.state.account = account
    app.state.phone = phone
    app.state.watcher = watcher

    yield

    watcher_task.cancel()
    with suppress(asyncio.CancelledError):
        await watcher_task
    endpoint.destroy()


app = FastAPI(lifespan=lifespan)

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def get_root(request: Request):
    call_count = len(app.state.account.calls)
    return templates.TemplateResponse(
        request=request, name="index.html", context={"call_count": call_count}
    )


@app.get("/debug")
async def get_debug():
    return f"{len(app.state.account.calls)} calls"


@app.get("/debug/tap")
async def get_debug_tap(request: Request):
    return request.app.state.watcher.current_state()


@app.get("/debug/announce-test")
async def get_debug_announce_test(request: Request):
    stocktype = {
        "key": "stocktype/test",
        "manufacturer": "DEYA",
        "name": "I Got You!",
        "abv": "8.0",
        "fullname": "DEYA I Got You! (8.0% ABV)",
    }
    await request.app.state.watcher.announce(stocktype)
    return "announcement was made"


@app.get("/debug/call/{to}")
async def get_debug_call(request: Request, to: int):
    await request.app.state.phone.call(to, outgoing_handler)
    return "call was completed"


@app.get("/debug/test")
async def get_debug_test(request: Request):
    tts = await request.app.state.phone.tts("Hello and goodbye!!")

    async def handler(call: Call):
        await call.play(tts.name)

    numbers = [5288]
    await asyncio.gather(
        *[request.app.state.phone.call(number, handler) for number in numbers]
    )
    return "calls were made"
