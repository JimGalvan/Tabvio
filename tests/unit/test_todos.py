import json
import unittest

from strands.agent.state import AgentState
from strands.hooks import BeforeModelCallEvent, BeforeToolsEvent

from tabvio.agents.strands.browser_agent.todos import (
    TODO_STATE_KEY,
    TodoPromptHook,
    build_write_todos,
    render_todos,
)

BASE_PROMPT = "You are a web browser agent."


class FakeAgent:
    def __init__(self, system_prompt=BASE_PROMPT):
        self.system_prompt = system_prompt
        self.state = AgentState()


class FakeToolContext:
    def __init__(self, agent):
        self.agent = agent


class RecordingChannel:
    def __init__(self):
        self.published = []

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))


def build_tool():
    channel = RecordingChannel()
    return build_write_todos(channel)._tool_func, channel


def shopping_list():
    return [
        {"content": "Sign in to Walmart", "status": "completed"},
        {"content": "Add bananas", "status": "in_progress"},
        {"content": "Add 60 eggs", "status": "pending"},
    ]


class WriteTodosTest(unittest.TestCase):
    def test_stores_the_list_in_agent_state(self):
        write_todos, _ = build_tool()
        agent = FakeAgent()

        write_todos(todos=shopping_list(), tool_context=FakeToolContext(agent))

        self.assertEqual(agent.state.get(TODO_STATE_KEY), shopping_list())

    def test_returns_the_rendered_list(self):
        write_todos, _ = build_tool()

        result = write_todos(
            todos=shopping_list(), tool_context=FakeToolContext(FakeAgent())
        )

        self.assertIn("[x] Sign in to Walmart", result)
        self.assertIn("[~] Add bananas", result)
        self.assertIn("[ ] Add 60 eggs", result)

    def test_replaces_the_whole_list(self):
        write_todos, _ = build_tool()
        agent = FakeAgent()
        context = FakeToolContext(agent)

        write_todos(todos=shopping_list(), tool_context=context)
        write_todos(
            todos=[{"content": "Choose a delivery slot", "status": "pending"}],
            tool_context=context,
        )

        stored = agent.state.get(TODO_STATE_KEY)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["content"], "Choose a delivery slot")

    def test_publishes_the_list_for_the_live_view(self):
        write_todos, channel = build_tool()

        write_todos(todos=shopping_list(), tool_context=FakeToolContext(FakeAgent()))

        self.assertEqual(channel.published, [("task.todos", {"todos": shopping_list()})])

    def test_rejects_an_unknown_status_without_touching_state(self):
        write_todos, channel = build_tool()
        agent = FakeAgent()

        result = write_todos(
            todos=[{"content": "Add bananas", "status": "almost"}],
            tool_context=FakeToolContext(agent),
        )

        self.assertEqual(json.loads(result)["kind"], "validation_error")
        self.assertIsNone(agent.state.get(TODO_STATE_KEY))
        self.assertEqual(channel.published, [])


class TodoPromptTest(unittest.TestCase):
    def test_appends_the_current_list_to_the_prompt(self):
        agent = FakeAgent()
        agent.state.set(TODO_STATE_KEY, shopping_list())
        hook = TodoPromptHook(BASE_PROMPT)

        hook.show_current_todos(BeforeModelCallEvent(agent=agent))

        self.assertTrue(agent.system_prompt.startswith(BASE_PROMPT))
        self.assertIn("## Current task list", agent.system_prompt)
        self.assertIn("[ ] Add 60 eggs", agent.system_prompt)

    def test_leaves_the_prompt_alone_before_a_list_is_written(self):
        agent = FakeAgent()
        hook = TodoPromptHook(BASE_PROMPT)

        hook.show_current_todos(BeforeModelCallEvent(agent=agent))

        self.assertEqual(agent.system_prompt, BASE_PROMPT)

    def test_rebuilds_from_state_rather_than_stacking_lists(self):
        agent = FakeAgent()
        hook = TodoPromptHook(BASE_PROMPT)

        agent.state.set(TODO_STATE_KEY, shopping_list())
        hook.show_current_todos(BeforeModelCallEvent(agent=agent))
        agent.state.set(TODO_STATE_KEY, [{"content": "Add bananas", "status": "completed"}])
        hook.show_current_todos(BeforeModelCallEvent(agent=agent))

        self.assertEqual(agent.system_prompt.count("## Current task list"), 1)
        self.assertNotIn("Add 60 eggs", agent.system_prompt)

    def test_is_unchanged_while_the_list_is_unchanged(self):
        agent = FakeAgent()
        agent.state.set(TODO_STATE_KEY, shopping_list())
        hook = TodoPromptHook(BASE_PROMPT)

        hook.show_current_todos(BeforeModelCallEvent(agent=agent))
        first = agent.system_prompt
        hook.show_current_todos(BeforeModelCallEvent(agent=agent))

        self.assertEqual(agent.system_prompt, first)


class ParallelWriteTest(unittest.TestCase):
    def build_message(self, tool_names):
        content = []
        for name in tool_names:
            content.append({"toolUse": {"name": name, "toolUseId": name, "input": {}}})
        return {"role": "assistant", "content": content}

    def test_cancels_a_turn_that_writes_the_list_twice(self):
        hook = TodoPromptHook(BASE_PROMPT)
        event = BeforeToolsEvent(
            agent=FakeAgent(),
            message=self.build_message(["write_todos", "write_todos"]),
            invocation_state={},
        )

        hook.reject_parallel_writes(event)

        self.assertIn("cannot run twice", str(event.cancel))

    def test_allows_one_write_alongside_other_tools(self):
        hook = TodoPromptHook(BASE_PROMPT)
        event = BeforeToolsEvent(
            agent=FakeAgent(),
            message=self.build_message(["write_todos", "observe_page"]),
            invocation_state={},
        )

        hook.reject_parallel_writes(event)

        self.assertFalse(event.cancel)


class RenderTest(unittest.TestCase):
    def test_renders_nothing_when_the_list_is_empty_or_missing(self):
        self.assertEqual(render_todos(None), "")
        self.assertEqual(render_todos([]), "")


if __name__ == "__main__":
    unittest.main()
