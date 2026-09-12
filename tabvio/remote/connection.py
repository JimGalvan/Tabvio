import asyncio
from uuid import uuid4


class Connection:
    def __init__(self, send, receive, handle):
        self._send = send
        self._receive = receive
        self._handle = handle
        self._pending = {}
        self._handlers = set()
        self._send_lock = asyncio.Lock()
        self.closed = False

    async def send(self, message):
        async with self._send_lock:
            await self._send(message)

    async def call(self, method, params=None, timeout=60):
        if self.closed:
            raise ConnectionError("The agent connection is closed")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params or {}})
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self._pending.pop(request_id, None)

    async def listen(self):
        try:
            while True:
                message = await self._receive()
                if "method" in message:
                    if len(self._handlers) >= 32:
                        raise ConnectionError("Too many pending agent commands")
                    task = asyncio.create_task(self._respond(message))
                    self._handlers.add(task)
                    task.add_done_callback(self._handlers.discard)
                else:
                    future = self._pending.get(message.get("id"))
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        future.set_exception(RuntimeError(message["error"]))
                    else:
                        future.set_result(message.get("result"))
        finally:
            self.closed = True
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("The agent connection was lost"))
            tasks = list(self._handlers)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _respond(self, message):
        try:
            result = await self._handle(message["method"], message.get("params", {}))
            response = {"id": message["id"], "result": result}
        except asyncio.CancelledError:
            raise
        except Exception:
            response = {"id": message["id"], "error": "The agent command could not be completed"}
        try:
            await self.send(response)
        except Exception:
            self.closed = True
