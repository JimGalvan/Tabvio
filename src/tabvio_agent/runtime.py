from typing import Any

from langgraph.types import Command

from tabvio_agent import constants


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
