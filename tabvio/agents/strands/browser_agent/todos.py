import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from strands import tool
from strands.hooks import (
    BeforeModelCallEvent,
    BeforeToolsEvent,
    HookProvider,
    HookRegistry,
)
from strands.types.tools import ToolContext

from tabvio.agents.strands.browser_agent.schema import inline_references
from tabvio.agents.strands.shared.events import AgentEventChannel

TODO_STATE_KEY = "todos"
WRITE_TODOS_TOOL_NAME = "write_todos"

STATUS_MARKS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}

PARALLEL_CALL_ERROR = (
    "`write_todos` replaces the whole list, so it cannot run twice in one turn. "
    "No tool in that batch ran. Send one call with every item in it."
)


class Todo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=200)
    status: Literal["pending", "in_progress", "completed"]


class TodoList(BaseModel):
    todos: list[Todo] = Field(max_length=40)


TODO_LIST_SCHEMA = inline_references(TodoList.model_json_schema())


def render_todos(stored: Any) -> str:
    if not isinstance(stored, list) or not stored:
        return ""

    lines = []
    for item in stored:
        if not isinstance(item, dict):
            continue
        mark = STATUS_MARKS.get(item.get("status"), STATUS_MARKS["pending"])
        lines.append(f"{mark} {item.get('content', '')}")
    return "\n".join(lines)


def build_write_todos(channel: AgentEventChannel):
    @tool(inputSchema={"json": TODO_LIST_SCHEMA}, context=True)
    def write_todos(todos: list, tool_context: ToolContext) -> str:
        """Record the task list for this run and keep every item's status current."""
        try:
            validated = TodoList.model_validate({"todos": todos})
        except ValidationError as exception:
            return json.dumps(
                {
                    "ok": False,
                    "kind": "validation_error",
                    "error": str(exception),
                }
            )

        stored = validated.model_dump()["todos"]
        tool_context.agent.state.set(TODO_STATE_KEY, stored)
        channel.publish("task.todos", {"todos": stored})
        rendered = render_todos(stored)
        if not rendered:
            return "The task list is now empty."
        return f"The task list is now:\n{rendered}"

    return write_todos


def count_write_todos_calls(message: Any) -> int:
    if not isinstance(message, dict):
        return 0

    content = message.get("content")
    if not isinstance(content, list):
        return 0

    calls = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        use = block.get("toolUse")
        if isinstance(use, dict) and use.get("name") == WRITE_TODOS_TOOL_NAME:
            calls += 1
    return calls


class TodoPromptHook(HookProvider):
    """Keeps the current task list in the system prompt, and one writer per turn."""

    def __init__(self, base_prompt: str):
        self._base_prompt = base_prompt

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.show_current_todos)
        registry.add_callback(BeforeToolsEvent, self.reject_parallel_writes)

    def show_current_todos(self, event: BeforeModelCallEvent) -> None:
        rendered = render_todos(event.agent.state.get(TODO_STATE_KEY))
        if not rendered:
            event.agent.system_prompt = self._base_prompt
            return
        event.agent.system_prompt = (
            f"{self._base_prompt}\n\n## Current task list\n\n{rendered}"
        )

    def reject_parallel_writes(self, event: BeforeToolsEvent) -> None:
        if count_write_todos_calls(event.message) > 1:
            event.cancel = PARALLEL_CALL_ERROR
