import asyncio
from typing import Any


class AgentEventChannel:
    def __init__(self):
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def reset(self) -> None:
        self._queue = asyncio.Queue()

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self._queue.put_nowait(
            {"kind": "custom", "event_type": event_type, "payload": payload}
        )

    def publish_agent_event(self, event: dict[str, Any]) -> None:
        self._queue.put_nowait({"kind": "agent", "event": event})

    def finish(self) -> None:
        self._queue.put_nowait({"kind": "finished"})

    async def next_item(self) -> dict[str, Any]:
        return await self._queue.get()
