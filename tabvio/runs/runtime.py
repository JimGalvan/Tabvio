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
    read_browser_channel_setting,
    read_browser_trace_setting,
)
from tabvio.credentials.agent_provider import CredentialServiceProvider
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
        if stream_type == "updates":
            return self._update_item(data)
        return None

    def _update_item(self, data: dict[str, Any]) -> dict[str, Any] | None:
        if constants.AGENT_INTERRUPT_KEY in data:
            return {"kind": "interrupt"}
        for update in data.values():
            if update and constants.TODOS_STATE_KEY in update:
                todos = update[constants.TODOS_STATE_KEY]
                return {"kind": "custom", "event_type": constants.TODOS_EVENT_TYPE, "payload": {"todos": todos}}
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
    )
    agent_context = AgentContext()
    sensitive_inputs = SensitiveInputChannel()
    credential_provider = None
    if credential_service is not None and credential_ids and user_id is not None:
        credential_provider = CredentialServiceProvider(credential_service, user_id, credential_ids)
    agent = build_browser_agent(browser, sensitive_inputs, credential_provider=credential_provider)
    return LangChainAgentRuntime(agent, browser, agent_context, sensitive_inputs, thread_id)
