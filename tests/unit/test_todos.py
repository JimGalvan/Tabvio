import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from tabvio.agents.browser_agent.browser_agent import build_browser_agent
from tabvio.agents.browser_agent.prompts import TODO_SYSTEM_PROMPT
from tabvio.browser.session import BrowserSession
from tabvio.runs.runtime import LangChainAgentRuntime
from tabvio.runs.sensitive_input import SensitiveInputChannel


class ScriptedModel(GenericFakeChatModel):
    disable_streaming: bool = True

    def bind_tools(self, tools, **kwargs):
        return self


def shopping_list():
    return [
        {"content": "Sign in to Walmart", "status": "completed"},
        {"content": "Add bananas", "status": "in_progress"},
        {"content": "Add 60 eggs", "status": "pending"},
    ]


def write_todos_call(call_id):
    return {"name": "write_todos", "args": {"todos": shopping_list()}, "id": call_id}


def build_runtime(*messages):
    model = ScriptedModel(messages=iter(messages))
    agent = create_deep_agent(
        model=model,
        checkpointer=InMemorySaver(),
        middleware=[TodoListMiddleware(system_prompt=TODO_SYSTEM_PROMPT)],
    )
    return LangChainAgentRuntime(agent, browser=None, context=None, sensitive_inputs=None, thread_id=uuid4())


async def todo_events(runtime):
    events = []
    async for item in runtime.stream(runtime.start_input("buy groceries")):
        if item.get("event_type") == "task.todos":
            events.append(item)
    return events


class TodoEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_writing_todos_publishes_the_list(self) -> None:
        runtime = build_runtime(AIMessage(content="", tool_calls=[write_todos_call("w1")]), AIMessage(content="Done."))
        events = await todo_events(runtime)
        self.assertEqual(events, [{"kind": "custom", "event_type": "task.todos", "payload": {"todos": shopping_list()}}])

    async def test_parallel_writes_are_rejected_without_publishing(self) -> None:
        parallel = AIMessage(content="", tool_calls=[write_todos_call("a"), write_todos_call("b")])
        runtime = build_runtime(parallel, AIMessage(content="Done."))
        self.assertEqual(await todo_events(runtime), [])


class BrowserAgentTodoTests(unittest.TestCase):
    def test_the_browser_agent_offers_write_todos(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            agent = build_browser_agent(BrowserSession(), SensitiveInputChannel())
        tool_names = agent.nodes["tools"].bound.tools_by_name
        self.assertIn("write_todos", tool_names)

    def test_the_todo_prompt_carries_the_browsing_guidance(self) -> None:
        self.assertIn("Mark an item `completed` only once an observation proves it", TODO_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
