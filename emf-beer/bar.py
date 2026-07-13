import asyncio
import json
import logging

import httpx
import websockets

from .settings import settings
from .sip import Call, Phone

logger = logging.getLogger(__name__)

ROBOT_ARMS_SLUG = "robotarms"
WATCHED_LINE_PREFIXES = ("pump", "tap", "cider")


class BarWatcher:
    def __init__(self, phone: Phone):
        self._phone = phone
        self._lines: dict[int, str] = {}
        self._current: dict[int, dict | None] = {}
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._connected = False

    async def run(self) -> None:
        worker = asyncio.create_task(self._announce_worker())
        backoff = 1
        try:
            while True:
                self._connected = False
                try:
                    await self._connect_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Bar watcher connection error", exc_info=True)
                backoff = 1 if self._connected else min(backoff * 2, 30)
                await asyncio.sleep(backoff)
        finally:
            worker.cancel()

    async def _connect_once(self) -> None:
        line_ids = await self._fetch_line_ids()
        async with websockets.connect(settings.bar_ws_url) as ws:
            for line_id in line_ids:
                await ws.send(f"SUBSCRIBE stockline/{line_id}")
            self._connected = True
            logger.info("Subscribed to %d Robot Arms draught lines", len(line_ids))
            async for raw in ws:
                self._handle(raw)

    async def _fetch_line_ids(self) -> list[int]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(settings.bar_stocklines_url)
            resp.raise_for_status()
            data = resp.json()

        self._lines = {}
        for line in data.get("stocklines", []):
            location = line.get("location_display") or {}
            name = line.get("name", "")
            if (
                line.get("linetype") == "regular"
                and location.get("slug") == ROBOT_ARMS_SLUG
                and name.lower().startswith(WATCHED_LINE_PREFIXES)
            ):
                self._lines[line["id"]] = name
        return list(self._lines)

    def _handle(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict) or msg.get("type") != "stockline":
            return
        line_id = msg.get("id")
        if line_id not in self._lines:
            return

        stocktype = self._effective_stocktype(msg)
        new_key = stocktype.get("key") if stocktype else None

        first_time = line_id not in self._current
        previous = self._current.get(line_id)
        prev_key = previous.get("key") if previous else None
        self._current[line_id] = stocktype

        if first_time:
            return

        if stocktype is not None and new_key is not None and new_key != prev_key:
            logger.info(
                "New beer on %s: %s", self._lines[line_id], stocktype.get("fullname")
            )
            self._queue.put_nowait(stocktype)

    @staticmethod
    def _effective_stocktype(msg: dict) -> dict | None:
        item = msg.get("stockitem")
        if item and item.get("stocktype"):
            return item["stocktype"]
        return msg.get("stocktype")

    async def _announce_worker(self) -> None:
        while True:
            stocktype = await self._queue.get()
            try:
                await self.announce(stocktype)
            except Exception:
                logger.exception("Failed to announce new beer")
            finally:
                self._queue.task_done()

    async def announce(self, stocktype: dict) -> None:
        numbers = settings.announce_number_list
        text = self._announcement_text(stocktype)
        if not numbers:
            logger.warning(
                "New beer on tap, but no announce numbers configured: %s", text
            )
            return

        tts = await self._phone.tts(text)

        async def handler(call: Call) -> None:
            await call.play(tts.name)

        logger.info("Announcing '%s' to %s", text, numbers)
        await asyncio.gather(*[self._phone.call(n, handler) for n in numbers])

    @staticmethod
    def _announcement_text(stocktype: dict) -> str:
        parts = (stocktype.get("manufacturer"), stocktype.get("name"))
        beer = " ".join(p for p in parts if p) or "a new beer"
        text = f"New on tap at the Robot Arms: {beer}"
        abv = stocktype.get("abv")
        if abv:
            text += f", {abv} percent"
        return text + "."

    def current_state(self) -> dict:
        return {
            "connected": self._connected,
            "lines": [
                {
                    "id": line_id,
                    "name": name,
                    "beer": (self._current.get(line_id) or {}).get("fullname"),
                }
                for line_id, name in sorted(self._lines.items())
            ],
        }
