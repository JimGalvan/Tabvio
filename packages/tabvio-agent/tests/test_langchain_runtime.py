import unittest
from uuid import uuid4

from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt
from tabvio_agent.runtime import LangChainAgentRuntime, extract_text
from tabvio_agent.shared.events import publish_custom_event


class ScriptedModel(GenericFakeChatModel):
    disable_streaming: bool = True

    def bind_tools(self, tools, **kwargs):
        return self


class FailingModel(ScriptedModel):
    def _generate(self, *args, **kwargs):
        raise RuntimeError("model died")


@tool
def ask_person(question: str) -> str:
    """Ask the person a question and wait for the answer."""
    publish_custom_event("input.required", {"question": question})
    return f"The person answered: {interrupt({'question': question})}"


@tool
def click_button() -> str:
    """Click the button on the page."""
    publish_custom_event("browser.action.started", {"action": "click"})
    return "clicked"


def scripted(*messages):
    return ScriptedModel(messages=iter(messages))


def call(name, args=None):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": str(uuid4())}])


def build_runtime(model, subagent_model=None):
    subagents = []
    if subagent_model:
        subagents.append({"name": "helper", "description": "Finds things.", "system_prompt": "Find it.", "model": subagent_model})
    agent = create_deep_agent(model=model, tools=[ask_person, click_button], checkpointer=InMemorySaver(), subagents=subagents)
    return LangChainAgentRuntime(agent, browser=None, context=None, sensitive_inputs=None, thread_id=uuid4())


async def collect(runtime, agent_input):
    items = []
    async for item in runtime.stream(agent_input):
        items.append(item)
    return items


def messages_in(items):
    texts = []
    for item in items:
        if item["kind"] == "message":
            texts.append(item["text"])
    return texts


class LangChainRuntimeStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_assistant_text_becomes_message_items(self) -> None:
        runtime = build_runtime(scripted(AIMessage(content="The page code is HOME-7F3.")))
        items = await collect(runtime, runtime.start_input("read the page code"))
        self.assertEqual(items, [{"kind": "message", "text": "The page code is HOME-7F3."}])

    async def test_tool_events_reach_the_stream_in_order(self) -> None:
        runtime = build_runtime(scripted(call("click_button"), AIMessage(content="Done.")))
        items = await collect(runtime, runtime.start_input("click it"))
        self.assertEqual(
            items,
            [
                {"kind": "custom", "event_type": "browser.action.started", "payload": {"action": "click"}},
                {"kind": "message", "text": "Done."},
            ],
        )

    async def test_subagent_text_stays_out_of_the_stream(self) -> None:
        main_model = scripted(
            call("task", {"description": "find the price", "subagent_type": "helper"}),
            AIMessage(content="It costs 20 dollars."),
        )
        runtime = build_runtime(main_model, scripted(AIMessage(content="helper notes")))
        items = await collect(runtime, runtime.start_input("find the price"))
        self.assertEqual(messages_in(items), ["It costs 20 dollars."])

    async def test_a_question_interrupts_and_the_answer_resumes_the_run(self) -> None:
        runtime = build_runtime(scripted(call("ask_person", {"question": "Which size?"}), AIMessage(content="Bought size M.")))
        first_turn = await collect(runtime, runtime.start_input("buy a shirt"))
        self.assertEqual(
            first_turn,
            [
                {"kind": "custom", "event_type": "input.required", "payload": {"question": "Which size?"}},
                {"kind": "interrupt"},
            ],
        )
        second_turn = await collect(runtime, runtime.resume_input("M"))
        self.assertEqual(messages_in(second_turn), ["Bought size M."])
        self.assertEqual(await runtime.final_output(), "Bought size M.")

    async def test_narration_does_not_leak_into_the_final_output(self) -> None:
        model = scripted(
            AIMessage(content="Let me click it.", tool_calls=[{"name": "click_button", "args": {}, "id": "c1"}]),
            AIMessage(content="The button is pressed."),
        )
        runtime = build_runtime(model)
        items = await collect(runtime, runtime.start_input("press the button"))
        self.assertEqual(messages_in(items), ["Let me click it.", "The button is pressed."])
        self.assertEqual(await runtime.final_output(), "The button is pressed.")

    async def test_a_failing_model_surfaces_its_error(self) -> None:
        runtime = build_runtime(FailingModel(messages=iter([])))
        with self.assertRaisesRegex(RuntimeError, "model died"):
            await collect(runtime, runtime.start_input("do the thing"))

    async def test_final_output_is_empty_before_any_turn(self) -> None:
        runtime = build_runtime(scripted())
        self.assertEqual(await runtime.final_output(), "")


class LangChainRuntimeInputTests(unittest.TestCase):
    def test_start_input_is_a_user_message(self) -> None:
        runtime = build_runtime(scripted())
        self.assertEqual(runtime.start_input("go"), {"messages": [{"role": "user", "content": "go"}]})

    def test_resume_input_carries_the_value(self) -> None:
        runtime = build_runtime(scripted())
        resume = runtime.resume_input({"entered": True, "submitted": "Verify"})
        self.assertIsInstance(resume, Command)
        self.assertEqual(resume.resume, {"entered": True, "submitted": "Verify"})


class ExtractTextTests(unittest.TestCase):
    def test_plain_text_is_returned_as_is(self) -> None:
        self.assertEqual(extract_text("Task complete"), "Task complete")

    def test_text_blocks_are_joined(self) -> None:
        content = [{"type": "text", "text": "Page code is "}, {"type": "text", "text": "HOME-7F3"}]
        self.assertEqual(extract_text(content), "Page code is HOME-7F3")

    def test_blocks_without_text_are_skipped(self) -> None:
        content = [{"type": "tool_use", "name": "observe_page"}, {"type": "text", "text": "done"}]
        self.assertEqual(extract_text(content), "done")

    def test_anything_else_is_empty(self) -> None:
        self.assertEqual(extract_text(None), "")
        self.assertEqual(extract_text({"text": "not a list"}), "")
