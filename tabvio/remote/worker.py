import asyncio
import base64
from dataclasses import asdict
from uuid import UUID

from bedrock_agentcore import BedrockAgentCoreApp
from opentelemetry import baggage, context

from tabvio.agents.strands.shared.telemetry import flush_telemetry
from tabvio.credentials.models import CredentialMetadata, CredentialSecret
from tabvio.remote.connection import Connection
from tabvio.runs.runtime import build_local_agent_runtime

app = BedrockAgentCoreApp()

BROWSER_METHODS = {
    "capture_screen_frame", "fill_sensitive", "user_click", "user_mouse_down",
    "user_mouse_up", "user_scroll", "user_key", "user_text",
}


class WorkerCredentials:
    def __init__(self, connection, loop):
        self.connection = connection
        self.loop = loop

    def require_selected(self, credential_ids, user_id):
        result = self._call("credentials.list", {"ids": [str(value) for value in credential_ids]})
        return [CredentialMetadata.model_validate(item) for item in result]

    def resolve_for_domain(self, credential_id, user_id, hostname):
        result = self._call("credentials.resolve", {"id": str(credential_id), "hostname": hostname})
        return CredentialSecret.model_validate(result)

    def _call(self, method, params):
        future = asyncio.run_coroutine_threadsafe(self.connection.call(method, params), self.loop)
        return future.result(timeout=65)


class Worker:
    def __init__(self, websocket):
        self.connection = Connection(websocket.send_json, websocket.receive_json, self.handle)
        self.runtime = None
        self.streaming = False
        self.run_id = None

    async def handle(self, method, params):
        if method == "initialize":
            if self.runtime is not None:
                raise RuntimeError("An agent is already running in this session")
            self.runtime = build_local_agent_runtime(
                UUID(params["thread_id"]),
                UUID(params["user_id"]) if params.get("user_id") else None,
                tuple(UUID(value) for value in params.get("credential_ids", [])),
                WorkerCredentials(self.connection, asyncio.get_running_loop()),
            )
            self.run_id = str(UUID(params["thread_id"]))
            return {"ready": True}
        if self.runtime is None:
            raise RuntimeError("The agent has not been initialized")
        if method == "stream":
            return await self.stream(params)
        if method == "close":
            await self.runtime.browser.close()
            return None
        if method in BROWSER_METHODS:
            result = await getattr(self.runtime.browser, method)(*params.get("args", []))
            if isinstance(result, bytes):
                return base64.b64encode(result).decode("ascii")
            return result
        raise ValueError("Unknown agent command")

    async def stream(self, params):
        if self.streaming:
            raise RuntimeError("The agent is already processing a task")
        self.streaming = True
        token = context.attach(baggage.set_baggage("session.id", self.run_id))
        try:
            agent_input = (
                self.runtime.resume_input(params["input"])
                if params.get("resume") else self.runtime.start_input(params["input"])
            )
            async for event in self.runtime.stream(agent_input):
                pending = self.runtime.sensitive_inputs.pending
                state = asdict(pending) if pending else None
                if state:
                    state["id"] = str(state["id"])
                await self.connection.call("event", {"event": event, "pending": state})
            return {"output": await self.runtime.final_output()}
        finally:
            self.streaming = False
            context.detach(token)
            await asyncio.to_thread(flush_telemetry)


@app.websocket
async def connect(websocket, context):
    await websocket.accept()
    worker = Worker(websocket)
    try:
        async with asyncio.timeout(1800):
            await worker.connection.listen()
    finally:
        if worker.runtime is not None:
            await worker.runtime.browser.close()


if __name__ == "__main__":
    app.run()
