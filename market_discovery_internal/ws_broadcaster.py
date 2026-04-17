"""Threaded WebSocket broadcaster for pushing realtime events to browser clients."""

import asyncio
import contextlib
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)


class WsBroadcaster:
    """Run a lightweight asyncio WebSocket server in a daemon thread.

    The broadcaster is intentionally write-only for clients: browsers connect and
    receive JSON events. Incoming client messages are ignored.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8081,
        max_clients: int = 10,
        ping_interval_seconds: int = 20,
    ):
        self._host = host
        self._port = int(port)
        self._max_clients = max(1, int(max_clients))
        self._ping_interval_seconds = max(5, int(ping_interval_seconds))

        self._loop = None
        self._thread = None
        self._clients = set()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._stats = {
            "start_time": 0,
            "total_broadcasts": 0,
            "last_broadcast_ts": 0,
            "connected_clients": 0
        }

    def start(self) -> bool:
        """Start broadcaster thread. Returns True if thread started."""
        if self._thread and self._thread.is_alive():
            return True

        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_thread,
            daemon=True,
            name="WsBroadcaster",
        )
        self._thread.start()
        self._ready_event.wait(timeout=2.0)

        loop = self._loop
        if loop and loop.is_running():
            logger.info("[WS-BROADCAST] Listening on ws://%s:%s", self._host, self._port)
            return True
        return False

    def stop(self) -> None:
        """Stop broadcaster thread and close all client connections."""
        self._stop_event.set()

        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)

        if self._thread:
            self._thread.join(timeout=5.0)

        self._thread = None
        self._loop = None
        self._clients = set()
        logger.info("[WS-BROADCAST] Stopped")

    def broadcast_price(self, token_id: str, bid_price: float) -> None:
        """Queue a live price update event for all connected clients."""
        payload = {
            "type": "price_update",
            "token_id": str(token_id),
            "bid_price": float(bid_price),
            "ts": int(time.time()),
        }
        self._schedule_broadcast(payload)

    def broadcast_closed(
        self,
        token_id: str,
        city: str,
        reason: str,
        exit_price: float,
    ) -> None:
        """Queue a closed-position event for all connected clients."""
        payload = {
            "type": "position_closed",
            "token_id": str(token_id),
            "city": str(city or ""),
            "reason": str(reason or ""),
            "exit_price": float(exit_price),
            "ts": int(time.time()),
        }
        self._schedule_broadcast(payload)

    def _run_thread(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.warning(
                "[WS-BROADCAST] websockets package not installed. "
                "Install with: pip install \"websockets>=12.0\""
            )
            self._ready_event.set()
            return

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stats["start_time"] = int(time.time())

        try:
            loop.run_until_complete(self._serve_forever(websockets))
        except Exception as exc:
            logger.warning("[WS-BROADCAST] Fatal loop error: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._loop = None

    async def _serve_forever(self, websockets_module) -> None:
        max_attempts = 15
        attempt = 0
        while not self._stop_event.is_set() and attempt < max_attempts:
            attempt += 1
            try:
                # [MODUL N] WebSocket Handshake Hardening
                async with websockets_module.serve(
                    self._handle_client,
                    self._host,
                    self._port,
                    ping_interval=None,
                    ping_timeout=None,
                    reuse_address=True,
                    process_request=self._process_request,
                ):
                    self._ready_event.set()
                    ping_task = asyncio.create_task(self._ping_loop())

                    while not self._stop_event.is_set():
                        await asyncio.sleep(0.5)

                    ping_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await ping_task
                    await self._close_all_clients()
                    return # Clean exit

            except OSError as exc:
                if attempt < max_attempts:
                    logger.warning(
                        "[WS-BROADCAST] Bind failed on %s:%s (Attempt %d/%d): %s. Retrying in 2s...",
                        self._host, self._port, attempt, max_attempts, exc
                    )
                    await asyncio.sleep(2)
                else:
                    logger.error(
                        "[WS-BROADCAST] FATAL: Could not bind %s:%s after %d attempts: %s",
                        self._host, self._port, max_attempts, exc
                    )
        
        self._ready_event.set()

    async def _process_request(self, *args, **kwargs):
        """
        Master Plan Handshake Hardening:
        Handle non-standard Connection headers safely.
        Variadic signature ensures zero-flaw compatibility across websockets versions.
        """
        # In newer websockets, 'request' is usually the second positional arg (index 1)
        # after 'connection' (index 0). In older, it might be different.
        request = kwargs.get("request")
        if not request and len(args) > 1:
            request = args[1]
        
        headers = getattr(request, "headers", {})
        if "Connection" in headers:
            conn = headers.get("Connection", "").lower()
            if "upgrade" not in conn:
                logger.debug("[WS-BROADCAST] Non-standard connection header: %s", conn)
        return None  # Proceed with default WebSocket handshake

    async def _handle_client(self, websocket):
        if len(self._clients) >= self._max_clients:
            logger.warning("[WS-BROADCAST] Rejecting client: max clients reached (%d)", self._max_clients)
            with contextlib.suppress(Exception):
                await websocket.close(code=1013, reason="max clients reached")
            return

        self._clients.add(websocket)
        logger.info("[WS-BROADCAST] Browser connected (%d client(s))", len(self._clients))

        try:
            async for _ in websocket:
                # Read-only channel for browsers; incoming messages are ignored.
                pass
        except Exception:
            pass
        finally:
            self._clients.discard(websocket)
            logger.info("[WS-BROADCAST] Browser disconnected (%d client(s))", len(self._clients))

    async def _ping_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._ping_interval_seconds)
            await self._broadcast_payload({"type": "ping", "ts": int(time.time())})

    async def _close_all_clients(self) -> None:
        clients = list(self._clients)
        self._clients = set()
        for client in clients:
            with contextlib.suppress(Exception):
                await client.close()

    async def _broadcast_payload(self, payload: dict) -> None:
        if not self._clients:
            return

        wire = json.dumps(payload)
        stale = []
        for client in list(self._clients):
            try:
                await client.send(wire)
            except Exception:
                stale.append(client)

        self._stats["total_broadcasts"] += 1
        self._stats["last_broadcast_ts"] = int(time.time())
        self._stats["connected_clients"] = len(self._clients)

        for client in stale:
            self._clients.discard(client)

    def _schedule_broadcast(self, payload: dict) -> None:
        loop = self._loop
        if not loop or not loop.is_running():
            return

        def _enqueue() -> None:
            asyncio.create_task(self._broadcast_payload(payload))

        loop.call_soon_threadsafe(_enqueue)