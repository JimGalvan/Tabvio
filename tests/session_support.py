import asyncio
from unittest.mock import AsyncMock

from tabvio_agent.events import RunCancelledEvent, RunCompletedEvent, SessionStatusEvent


class StubSession:
    def __init__(self, complete_turns=True):
        self.inputs = []
        self._events = asyncio.Queue()
        self._frames = asyncio.Queue()
        self._latest_frame = None
        self._closed = asyncio.Event()
        self.complete_turns = complete_turns
        self.close = AsyncMock(side_effect=self._close)

    async def start(self, task):
        self.inputs.append(task)
        if self.complete_turns:
            await self._events.put(SessionStatusEvent("running"))
            await self._events.put(RunCompletedEvent(f"Result {len(self.inputs)}"))
            await self._events.put(SessionStatusEvent("ready_for_follow_up"))

    async def follow_up(self, task):
        await self.start(task)

    async def cancel(self):
        await self._events.put(RunCancelledEvent())
        await self._events.put(SessionStatusEvent("cancelled"))
        await self.close()

    async def _close(self):
        if not self._closed.is_set():
            self._closed.set()
            await self._events.put(None)
            await self._frames.put(None)

    async def events(self):
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def emit_frame(self, frame):
        self._latest_frame = frame
        await self._frames.put(frame)

    async def screen_frames(self):
        while True:
            frame = await self._frames.get()
            if frame is None:
                return
            yield frame

    def get_latest_frame(self):
        return self._latest_frame
