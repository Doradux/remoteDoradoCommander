"""Controlled bursts against the device queue, no live remote commands."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from remote_dorado.agent_transport import serve_agent_requests


class FakeWebSocket:
    def __init__(self, messages, *, delay=0.0):
        self.messages = messages
        self.delay = delay
        self.responses = []

    async def __aiter__(self):
        for message in self.messages:
            yield message
        await asyncio.sleep(self.delay)

    async def send(self, payload):
        self.responses.append(json.loads(payload))


def test_burst_is_capped_and_extra_requests_get_busy_response():
    concurrent = 0
    peak = 0
    lock = threading.Lock()

    def slow_dispatch(op, args):
        nonlocal concurrent, peak
        with lock:
            concurrent += 1
            peak = max(peak, concurrent)
        time.sleep(0.04)
        with lock:
            concurrent -= 1
        return {"operation": op, "n": args["n"]}

    websocket = FakeWebSocket(
        [json.dumps({"id": str(i), "op": "ping", "args": {"n": i}})
         for i in range(40)], delay=0.27,
    )
    asyncio.run(serve_agent_requests(websocket, slow_dispatch, workers=2, queue_size=3))
    responses = websocket.responses
    assert peak <= 2
    busy = [r for r in responses if r.get("error", "").startswith("Device busy")]
    assert len(busy) >= 30
    success = [r for r in responses if "result" in r]
    assert success
    assert not set(r["id"] for r in success) & set(r["id"] for r in busy)


def test_invalid_input_is_rejected_before_dispatch():
    called = []

    def dispatch(op, args):
        called.append(op)
        return {"ok": True}

    ws = FakeWebSocket([
        "{not valid",
        json.dumps([]),
        json.dumps({"id": "bad-args", "op": "ping", "args": []}),
        json.dumps({"id": "bad-operation", "op": 5, "args": {}}),
        json.dumps({"id": "a" * 129, "op": "ping", "args": {}}),
        json.dumps({"id": "good", "op": "ping", "args": {}}),
    ], delay=0.1)
    asyncio.run(serve_agent_requests(ws, dispatch, workers=1, queue_size=3))
    assert called == ["ping"]
    assert {row["id"] for row in ws.responses} == {"bad-args", "bad-operation", "good"}


def test_invalid_limits_fail_without_starting_workers():
    ws = FakeWebSocket([])
    import pytest
    with pytest.raises(ValueError, match="limits"):
        asyncio.run(serve_agent_requests(ws, lambda op, args: {}, workers=100))
    with pytest.raises(ValueError, match="limits"):
        asyncio.run(serve_agent_requests(ws, lambda op, args: {}, queue_size=999))
