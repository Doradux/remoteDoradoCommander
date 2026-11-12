"""Bounded relay dispatch: saturated capacity returns busy without memory queues."""
import asyncio
from types import SimpleNamespace

import pytest

from remote_dorado.server import DeviceHub


def test_hub_returns_busy_before_enqueuing_socket_requests():
    async def scenario():
        hub = DeviceHub()
        sent = []
        class Socket:
            async def send_json(self, msg):
                sent.append(msg)
        hub.devices["test-pc"] = SimpleNamespace(
            socket=Socket(), pending={}, send_lock=asyncio.Lock(),
        )
        hub.capacity = asyncio.Semaphore(0)
        with pytest.raises(RuntimeError, match="Relay busy"):
            await hub.ask("test-pc", "ping", {})
        assert sent == []
        assert hub.devices["test-pc"].pending == {}
    asyncio.run(scenario())


def test_hub_releases_capacity_after_failed_device_response():
    async def scenario():
        hub = DeviceHub()
        class Socket:
            async def send_json(self, msg):
                future = hub.devices["test-pc"].pending[msg["id"]]
                future.set_result({"id": msg["id"], "error": "no permission"})
        hub.devices["test-pc"] = SimpleNamespace(
            socket=Socket(), pending={}, send_lock=asyncio.Lock(),
        )
        hub.capacity = asyncio.Semaphore(1)
        for _ in range(3):
            with pytest.raises(RuntimeError, match="no permission"):
                await hub.ask("test-pc", "execute_command", {})
        assert hub.capacity._value == 1
        assert hub.devices["test-pc"].pending == {}
    asyncio.run(scenario())
