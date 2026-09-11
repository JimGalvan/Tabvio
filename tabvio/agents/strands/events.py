import asyncio
from typing import Any

# Strands has no ambient stream writer, so tools and their helpers publish here
# instead. Agent events land on the same queue so the run sees one ordered stream
# and a tool's progress arrives while the tool is still running.


class AgentEventChannel:
    def __init__(self):
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def reset(self) -> None:
        # A consumer that stops early leaves items behind, and they would other
        # wise be read as the start of the next turn.
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
