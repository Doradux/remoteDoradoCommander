"""Bounded device command transport.

Only fixed-size workers can invoke remote operations; extra requests receive
explicit busy responses. This prevents a compromised relay or burst client
from creating unlimited asyncio tasks or OS executor work on a customer PC.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

MAX_AGENT_WORKERS = 8
MAX_AGENT_QUEUE = 24
MAX_REQUEST_ID = 128
MAX_OPERATION_NAME = 80


async def serve_agent_requests(
    ws: Any,
    dispatch: Callable[[str, dict[str, Any]], dict[str, Any]],
    *,
    workers: int = MAX_AGENT_WORKERS,
    queue_size: int = MAX_AGENT_QUEUE,
) -> None:
    if not 1 <= workers <= MAX_AGENT_WORKERS or not 1 <= queue_size <= MAX_AGENT_QUEUE:
        raise ValueError("Invalid agent transport limits")
    send_lock = asyncio.Lock()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)

    async def reply(item: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send(json.dumps(item, separators=(",", ":")))

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                try:
                    output = await asyncio.to_thread(dispatch, item["op"], item["args"])
                    result = {"id": item["id"], "result": output}
                except Exception as exc:
                    result = {"id": item["id"], "error": f"{type(exc).__name__}: {exc}"}
                await reply(result)
            finally:
                queue.task_done()

    pool = [asyncio.create_task(worker()) for _ in range(workers)]
    try:
        async for raw in ws:
            try:
                item = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(item, dict):
                continue
            identifier = item.get("id")
            operation = item.get("op")
            args = item.get("args", {})
            if (not isinstance(identifier, str) or not 1 <= len(identifier) <= MAX_REQUEST_ID
                    or not isinstance(operation, str) or not 1 <= len(operation) <= MAX_OPERATION_NAME
                    or not isinstance(args, dict)):
                # Malformed requests are never dispatched.
                if isinstance(identifier, str) and 1 <= len(identifier) <= MAX_REQUEST_ID:
                    await reply({"id": identifier, "error": "Invalid request"})
                continue
            packet = {"id": identifier, "op": operation, "args": args}
            try:
                queue.put_nowait(packet)
            except asyncio.QueueFull:
                await reply({"id": identifier, "error": "Device busy: retry later"})
    finally:
        for task in pool:
            task.cancel()
        await asyncio.gather(*pool, return_exceptions=True)
