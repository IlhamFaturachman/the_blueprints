import asyncio
import threading
import time

from market_discovery_internal.ws_broadcaster import WsBroadcaster


class _FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []
        self.closed = False

    async def send(self, message):
        if self.fail:
            raise RuntimeError("send failed")
        self.messages.append(message)

    async def close(self, code=None, reason=None):
        self.closed = True


def test_schedule_broadcast_no_loop_no_crash():
    broadcaster = WsBroadcaster()
    broadcaster.broadcast_price("tok1", 0.25)
    broadcaster.broadcast_closed("tok1", "city", "stop_loss", 0.24)


def test_broadcast_payload_sends_and_prunes_stale():
    broadcaster = WsBroadcaster()
    good = _FakeClient()
    bad = _FakeClient(fail=True)
    broadcaster._clients = {good, bad}

    asyncio.run(broadcaster._broadcast_payload({"type": "price_update", "token_id": "tok1", "bid_price": 0.22}))

    assert len(good.messages) == 1
    assert bad not in broadcaster._clients


def test_close_all_clients_closes_everything():
    broadcaster = WsBroadcaster()
    one = _FakeClient()
    two = _FakeClient()
    broadcaster._clients = {one, two}

    asyncio.run(broadcaster._close_all_clients())

    assert one.closed is True
    assert two.closed is True
    assert broadcaster._clients == set()


def test_schedule_broadcast_with_running_loop_enqueues_tasks():
    broadcaster = WsBroadcaster()
    sent = []

    async def fake_broadcast(payload):
        sent.append(payload)

    broadcaster._broadcast_payload = fake_broadcast

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        broadcaster._loop = loop
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    assert ready.wait(timeout=1.0)

    broadcaster.broadcast_price("tokA", 0.41)
    broadcaster.broadcast_closed("tokA", "new york", "take_profit_100pct", 0.50)

    timeout = time.time() + 1.0
    while len(sent) < 2 and time.time() < timeout:
        time.sleep(0.01)

    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=1.0)

    assert len(sent) == 2
    assert sent[0]["type"] == "price_update"
    assert sent[1]["type"] == "position_closed"
