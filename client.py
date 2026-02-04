from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientSession, WSMsgType
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass
class ZontCredentials:
    user: str
    password: str


class ZontWsClient:
    def __init__(self, hass: HomeAssistant, session: ClientSession, url: str, creds: ZontCredentials) -> None:
        self._hass = hass
        self._session = session
        self._url = url
        self._creds = creds

        self._ws = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._stop = asyncio.Event()

        self._runner_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None

        # pending responses keyed by id: deque of futures (на случай нескольких одинаковых id подряд)
        self._pending: dict[int, deque[asyncio.Future]] = defaultdict(deque)

        self._connected = asyncio.Event()

    async def start(self) -> None:
        if self._runner_task is None:
            self._runner_task = asyncio.create_task(self._runner())

    async def stop(self) -> None:
        self._stop.set()
        self._connected.clear()

        if self._runner_task:
            self._runner_task.cancel()
        if self._reader_task:
            self._reader_task.cancel()

        await self._close_ws()

        # отменим все ожидающие futures
        for q in self._pending.values():
            while q:
                fut = q.popleft()
                if not fut.done():
                    fut.cancel()

    async def _runner(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                await self._ensure_connected()
                self._connected.set()
                backoff = 1
                await asyncio.sleep(60)
            except Exception as e:
                self._connected.clear()
                _LOGGER.warning("WS disconnected: %s. Reconnecting in %ss", e, backoff)
                await self._close_ws()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _close_ws(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    async def _ensure_connected(self) -> None:
        if self._ws is not None and not self._ws.closed:
            return

        async with self._connect_lock:
            if self._ws is not None and not self._ws.closed:
                return

            ws = await self._session.ws_connect(self._url, heartbeat=30)

            # auth
            auth_payload = {"user": self._creds.user, "pass": self._creds.password}
            await ws.send_str(json.dumps(auth_payload, ensure_ascii=False, separators=(",", ":")))

            msg = await ws.receive(timeout=10)
            if msg.type != WSMsgType.TEXT:
                await ws.close()
                raise ConnectionError(f"Auth response not text: {msg.type}")

            try:
                data = json.loads(msg.data)
            except Exception:
                await ws.close()
                raise ConnectionError(f"Auth response not JSON: {msg.data}")

            if data.get("auth") != 200:
                await ws.close()
                raise PermissionError(f"Auth failed: {msg.data}")

            self._ws = ws
            self._start_reader()

    def _start_reader(self) -> None:
        if self._reader_task and not self._reader_task.done():
            return
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        """Единственный читатель."""
        assert self._ws is not None
        ws = self._ws
        try:
            while True:
                msg = await ws.receive()
                if msg.type == WSMsgType.TEXT:
                    await self._handle_incoming(msg.data)
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                    raise ConnectionError(f"WS closed/error: {msg.type}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._connected.clear()
            _LOGGER.warning("Reader stopped: %s", e)
            await self._close_ws()

    async def _handle_incoming(self, raw: str) -> None:
        """
        Пытаемся классифицировать сообщение:
        - ответ на команду: JSON с id, и кто-то ждёт по этому id
        - иначе: асинхронный event
        """
        try:
            data = json.loads(raw)
        except Exception:
            # не JSON — считаем событием
            self._fire_event({"raw": raw})
            return

        msg_id = data.get("id")
        if isinstance(msg_id, int) and self._pending.get(msg_id):
            fut = self._pending[msg_id].popleft()
            if not fut.done():
                fut.set_result(data)
            return

        # не похоже на ответ ожидаемой команды — считаем событием
        self._fire_event(data)

    def _fire_event(self, data: dict[str, Any]) -> None:
        # Публикуем событие в HA bus, чтобы можно было слушать автоматизацией
        self._hass.bus.async_fire("zont_ws_event", data)

    async def send_command(self, cmd_id: int, cmd: str, timeout: float = 10) -> Any:
        """
        Отправка команды + ожидание *именно ответа по этому id*.
        Асинхронные события не мешают — они идут в event bus.
        """
        await self._ensure_connected()
        if self._ws is None:
            raise ConnectionError("WS not connected")

        # Футура для ответа
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[cmd_id].append(fut)

        async with self._send_lock:
            payload = {"id": cmd_id, "cmd": cmd}
            await self._ws.send_str(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            # если таймаут/ошибка — убираем fut из pending
            try:
                q = self._pending.get(cmd_id)
                if q:
                    # удаляем именно этот future (на случай очереди)
                    self._pending[cmd_id] = deque(x for x in q if x is not fut)
            except Exception:
                pass
            raise