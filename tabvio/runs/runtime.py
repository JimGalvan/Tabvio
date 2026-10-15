import asyncio
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.types import Command

from tabvio.agents.browser_agent.browser_agent import build_browser_agent
from tabvio.agents.browser_agent.context import AgentContext
from tabvio.browser.session import BrowserSession
from tabvio.config import (
    TRACE_DIRECTORY,
    read_agentcore_browser_identifier,
    read_agentcore_session_timeout_seconds,
    read_aws_region_setting,
    read_browser_backend_setting,
    read_browser_channel_setting,
    read_browser_trace_setting,
)
from tabvio.credentials.service import CredentialService
from tabvio.runs import constants
from tabvio.runs.sensitive_input import SensitiveInputChannel

logging.getLogger("dotenv.main").setLevel(logging.ERROR)


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


class StrandsAgentRuntime:
    def __init__(self, agent, browser, context, sensitive_inputs, channel):
        self.agent = agent
        self.browser = browser
        self.context = context
        self.sensitive_inputs = sensitive_inputs
        self.channel = channel
        self.pending_interrupt_id: str | None = None
        self._last_message: Any = None

    def start_input(self, task: str) -> Any:
        return task

    def resume_input(self, value: Any) -> Any:
        if self.pending_interrupt_id is None:
            raise RuntimeError("The agent is not waiting on an interrupt")
        return [
            {
                "interruptResponse": {
                    "interruptId": self.pending_interrupt_id,
                    "response": value,
                }
            }
        ]

    async def stream(self, agent_input: Any):
        self.channel.reset()
        pump = asyncio.create_task(self._pump(agent_input))
        finished = False
        try:
            while True:
                item = await self.channel.next_item()
                if item["kind"] == "finished":
                    finished = True
                    break
                if item["kind"] == "custom":
                    yield item
                    continue

                normalized = self._normalize_agent_event(item["event"])
                if normalized is not None:
                    yield normalized
        finally:
            if finished:
                # Re-raises whatever the agent loop failed with.
                await pump
            else:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)

    async def final_output(self) -> str:
        if self._last_message is None:
            return ""
        if isinstance(self._last_message, dict):
            return extract_text(self._last_message.get("content"))
        return extract_text(getattr(self._last_message, "content", ""))

    async def _pump(self, agent_input: Any) -> None:
        try:
            async for event in self.agent.stream_async(agent_input):
                self.channel.publish_agent_event(event)
        finally:
            self.channel.finish()

    def _normalize_agent_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        text = event.get("data")
        if isinstance(text, str) and text:
            return {"kind": "message", "text": text}

        result = event.get("result")
        if result is None:
            return None

        self._last_message = getattr(result, "message", None)
        if getattr(result, "stop_reason", None) != "interrupt":
            self.pending_interrupt_id = None
            return None

        interrupts = getattr(result, "interrupts", None) or []
        if interrupts:
            self.pending_interrupt_id = interrupts[0].id
        return {"kind": "interrupt"}


class LangChainAgentRuntime:
    def __init__(self, agent, browser, context, sensitive_inputs, thread_id):
        self.agent = agent
        self.browser = browser
        self.context = context
        self.sensitive_inputs = sensitive_inputs
        self.config = {"configurable": {"thread_id": str(thread_id)}}

    def start_input(self, task: str) -> Any:
        return {"messages": [{"role": "user", "content": task}]}

    def resume_input(self, value: Any) -> Any:
        return Command(resume=value)

    async def stream(self, agent_input: Any):
        parts = self.agent.astream(
            agent_input,
            config=self.config,
            stream_mode=constants.AGENT_STREAM_MODES,
            version=constants.AGENT_STREAM_VERSION,
            context=self.context,
        )
        async for part in parts:
            item = self._normalize(part)
            if item:
                yield item

    async def final_output(self) -> str:
        state = await self.agent.aget_state(self.config)
        messages = state.values.get("messages", [])
        if not messages:
            return ""
        return extract_text(messages[-1].content)

    def _normalize(self, part: dict[str, Any]) -> dict[str, Any] | None:
        stream_type = part["type"]
        data = part["data"]
        if stream_type == "custom":
            return {"kind": "custom", "event_type": data["event_type"], "payload": data["payload"]}
        if stream_type == "messages":
            text = self._assistant_text(data)
            if text:
                return {"kind": "message", "text": text}
            return None
        if stream_type == "updates" and constants.AGENT_INTERRUPT_KEY in data:
            return {"kind": "interrupt"}
        return None

    def _assistant_text(self, data: tuple[Any, dict[str, Any]]) -> str:
        message, metadata = data
        if message.type not in constants.ASSISTANT_MESSAGE_TYPES:
            return ""
        if not self._is_from_main_agent(metadata):
            return ""
        return extract_text(message.content)

    def _is_from_main_agent(self, metadata: dict[str, Any]) -> bool:
        namespace = metadata.get("langgraph_checkpoint_ns") or ""
        return namespace.startswith(constants.MAIN_AGENT_MODEL_NAMESPACE)


def _trace_path_for_run(thread_id: UUID) -> Path | None:
    if not read_browser_trace_setting():
        return None
    return TRACE_DIRECTORY / f"{thread_id}.zip"


def _build_remote_browser():
    if read_browser_backend_setting() != "agentcore":
        return None

    # Imported here so a local run never needs boto3 credentials loaded.
    from tabvio.browser.agentcore import AgentCoreBrowser

    return AgentCoreBrowser(
        region=read_aws_region_setting(),
        identifier=read_agentcore_browser_identifier(),
        session_timeout_seconds=read_agentcore_session_timeout_seconds(
            constants.DEFAULT_AGENTCORE_SESSION_TIMEOUT_SECONDS
        ),
    )


def build_local_agent_runtime(
        thread_id: UUID,
        user_id: UUID | None,
        credential_ids: tuple[UUID, ...] = (),
        credential_service: CredentialService | None = None,
        headless: bool = True,
):
    browser = BrowserSession(
        headless=headless,
        trace_path=_trace_path_for_run(thread_id),
        browser_channel=read_browser_channel_setting(),
        remote_browser=_build_remote_browser(),
    )
    agent_context = AgentContext(user_id=user_id, credential_ids=credential_ids)
    sensitive_inputs = SensitiveInputChannel()
    agent = build_browser_agent(browser, sensitive_inputs, credential_service=credential_service)
    return LangChainAgentRuntime(agent, browser, agent_context, sensitive_inputs, thread_id)


def build_agent_runtime(thread_id, user_id, credential_ids=(), credential_service=None, headless=True, run_id=None):
    from tabvio.config import read_agentcore_runtime_arn

    runtime_arn = read_agentcore_runtime_arn()
    if runtime_arn:
        from tabvio.remote.runtime import RemoteAgentRuntime

        return RemoteAgentRuntime(runtime_arn, thread_id, user_id, credential_ids, credential_service, run_id=run_id)
    return build_local_agent_runtime(thread_id, user_id, credential_ids, credential_service, headless)
