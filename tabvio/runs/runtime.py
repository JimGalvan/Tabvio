import asyncio
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.types import Command

from tabvio.agents.browser_agent.browser_agent import build_browser_agent
from tabvio.agents.browser_agent.context import AgentContext
from tabvio.agents.strands.browser_agent import (
    build_browser_agent as build_strands_browser_agent,
)
from tabvio.agents.strands.events import AgentEventChannel
from tabvio.browser.session import BrowserSession
from tabvio.config import (
    TRACE_DIRECTORY,
    read_agent_engine_setting,
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

# Both runtimes turn their engine's stream into these items, so the run manager
# never sees a framework type: custom, message, interrupt.


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
        stream = self.agent.astream(
            agent_input,
            config=self.config,
            stream_mode=["messages", "custom", "updates"],
            version="v2",
            context=self.context,
        )
        async for part in stream:
            item = self._normalize(part)
            if item is not None:
                yield item

    async def final_output(self) -> str:
        try:
            state = await self.agent.aget_state(self.config)
            messages = state.values.get("messages", [])
            if messages:
                return extract_text(messages[-1].content)
        except Exception:
            pass
        return ""

    def _normalize(self, part: dict[str, Any]) -> dict[str, Any] | None:
        stream_type = part.get("type")
        data = part.get("data")

        if stream_type == "custom" and isinstance(data, dict):
            return {
                "kind": "custom",
                "event_type": data.get("event_type"),
                "payload": data.get("payload", {}),
            }

        if stream_type == "messages":
            text = self._message_text(data)
            if text:
                return {"kind": "message", "text": text}
            return None

        if stream_type == "updates" and self._contains_interrupt(data):
            return {"kind": "interrupt"}
        return None

    def _message_text(self, data: Any) -> str:
        if not isinstance(data, (tuple, list)) or not data:
            return ""
        message = data[0]
        if getattr(message, "type", None) not in {"ai", "assistant", "AIMessageChunk"}:
            return ""
        return extract_text(getattr(message, "content", ""))

    def _contains_interrupt(self, value: Any) -> bool:
        if isinstance(value, dict):
            if "__interrupt__" in value:
                return True
            for nested in value.values():
                if self._contains_interrupt(nested):
                    return True

        if isinstance(value, (list, tuple)):
            for nested in value:
                if self._contains_interrupt(nested):
                    return True
        return False


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


def build_agent_runtime(
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

    if read_agent_engine_setting() == "strands":
        channel = AgentEventChannel()
        agent = build_strands_browser_agent(
            browser,
            channel,
            agent_context,
            sensitive_inputs,
            credential_service=credential_service,
        )
        return StrandsAgentRuntime(
            agent, browser, agent_context, sensitive_inputs, channel
        )

    agent = build_browser_agent(
        browser,
        sensitive_inputs,
        credential_service=credential_service,
    )
    return LangChainAgentRuntime(
        agent, browser, agent_context, sensitive_inputs, thread_id
    )
