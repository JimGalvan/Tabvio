import asyncio
import base64
import json
import logging
from contextlib import suppress
from uuid import UUID

import boto3
from bedrock_agentcore.runtime import AgentCoreRuntimeClient
from websockets.asyncio.client import connect

from tabvio.config import read_aws_region_setting
from tabvio.remote.connection import Connection
from tabvio.runs.sensitive_input import PendingSensitiveInput, SensitiveInputChannel

logger = logging.getLogger(__name__)


class RemoteBrowser:
    def __init__(self, runtime):
        self.runtime = runtime

    async def capture_screen_frame(self, quality):
        if not self.runtime.ready:
            return None
        result = await self.runtime.connection.call("capture_screen_frame", {"args": [quality]})
        return base64.b64decode(result) if result else None

    async def fill_sensitive(self, element_index, code, submit_element_index):
        return await self._call("fill_sensitive", element_index, code, submit_element_index)

    async def user_click(self, x, y):
        return await self._call("user_click", x, y)

    async def user_mouse_down(self, x, y):
        return await self._call("user_mouse_down", x, y)

    async def user_mouse_up(self):
        return await self._call("user_mouse_up")

    async def user_scroll(self, x, y, delta_y):
        return await self._call("user_scroll", x, y, delta_y)

    async def user_key(self, key):
        return await self._call("user_key", key)

    async def user_text(self, text):
        return await self._call("user_text", text)

    async def _call(self, method, *args):
        return await self.runtime.connection.call(method, {"args": list(args)})

    async def close(self):
        await self.runtime.close()


class RemoteAgentRuntime:
    def __init__(self, runtime_arn, thread_id, user_id, credential_ids, credential_service):
        self.runtime_arn = runtime_arn
        self.thread_id = thread_id
        self.user_id = user_id
        self.credential_ids = credential_ids
        self.credential_service = credential_service
        self.browser = RemoteBrowser(self)
        self.sensitive_inputs = SensitiveInputChannel()
        self.connection = None
        self.ready = False
        self._socket = None
        self._listener = None
        self._events = asyncio.Queue(maxsize=256)
        self._output = ""
        self._closed = False
        self._connect_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._attempted = False

    def start_input(self, task):
        return {"input": task, "resume": False}

    def resume_input(self, value):
        return {"input": value, "resume": True}

    async def _connect(self):
        async with self._connect_lock:
            if self._closed:
                raise ConnectionError("This agent session has ended")
            if self.ready:
                return
            self._attempted = True
            client = AgentCoreRuntimeClient(region=read_aws_region_setting())
            url, headers = await asyncio.to_thread(
                client.generate_ws_connection, self.runtime_arn, str(self.thread_id)
            )
            self._socket = await connect(url, additional_headers=headers, max_size=8 * 1024 * 1024, open_timeout=120)

            async def send(message):
                await self._socket.send(json.dumps(message))

            async def receive():
                return json.loads(await self._socket.recv())

            self.connection = Connection(send, receive, self._handle)
            self._listener = asyncio.create_task(self.connection.listen())
            await self.connection.call("initialize", {
                "thread_id": str(self.thread_id),
                "user_id": str(self.user_id) if self.user_id else None,
                "credential_ids": [str(value) for value in self.credential_ids],
            })
            self.ready = True

    async def _handle(self, method, params):
        if method == "event":
            pending = params.get("pending")
            if pending:
                pending = {**pending, "id": UUID(pending["id"])}
            self.sensitive_inputs.replace_pending(PendingSensitiveInput(**pending) if pending else None)
            await self._events.put(params["event"])
            return None
        if not self.credential_service or not self.user_id:
            raise RuntimeError("Credential storage is not configured")
        if method == "credentials.list":
            ids = tuple(UUID(value) for value in params["ids"])
            if not set(ids).issubset(self.credential_ids):
                raise ValueError("Credential is not selected for this run")
            items = await asyncio.to_thread(self.credential_service.require_selected, ids, self.user_id)
            return [item.model_dump(mode="json") for item in items]
        if method == "credentials.resolve":
            credential_id = UUID(params["id"])
            if credential_id not in self.credential_ids:
                raise ValueError("Credential is not selected for this run")
            secret = await asyncio.to_thread(
                self.credential_service.resolve_for_domain, credential_id, self.user_id, params["hostname"]
            )
            return secret.model_dump(mode="json")
        raise ValueError("Unknown agent command")

    async def stream(self, agent_input):
        await self._connect()
        self._events = asyncio.Queue(maxsize=256)
        request = asyncio.create_task(self.connection.call("stream", agent_input, timeout=1800))
        next_event = None
        try:
            while True:
                if request.done() and self._events.empty():
                    result = await request
                    self._output = result["output"]
                    break
                next_event = asyncio.create_task(self._events.get())
                done, _ = await asyncio.wait([request, next_event], return_when=asyncio.FIRST_COMPLETED)
                if next_event in done:
                    yield next_event.result()
                else:
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
                next_event = None
        finally:
            if next_event is not None:
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
                await self.close()

    async def final_output(self):
        return self._output

    async def close(self):
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self.ready = False
            try:
                if self.connection and not self.connection.closed:
                    with suppress(Exception):
                        await self.connection.call("close", timeout=20)
            finally:
                if self._socket:
                    await self._socket.close()
                if self._listener:
                    self._listener.cancel()
                    await asyncio.gather(self._listener, return_exceptions=True)
                if self._attempted:
                    await asyncio.to_thread(self._stop_session)

    def _stop_session(self):
        client = boto3.client("bedrock-agentcore", region_name=read_aws_region_setting())
        try:
            client.stop_runtime_session(agentRuntimeArn=self.runtime_arn, runtimeSessionId=str(self.thread_id))
        except client.exceptions.ResourceNotFoundException:
            pass
