import unittest
from types import SimpleNamespace

from tabvio.agents.strands.shared.events import AgentEventChannel
from tabvio.runs.runtime import StrandsAgentRuntime, extract_text


def build_result(stop_reason, interrupts=None, text=""):
    return SimpleNamespace(
        stop_reason=stop_reason,
        interrupts=interrupts or [],
        message={"role": "assistant", "content": [{"text": text}]},
    )


class ScriptedAgent:
    """Replays a fixed list of Strands events, optionally failing part way."""

    def __init__(self, events, failure=None):
        self.events = events
        self.failure = failure
        self.inputs = []

    async def stream_async(self, agent_input):
        self.inputs.append(agent_input)
        for event in self.events:
            if callable(event):
                event()
                continue
            yield event
        if self.failure is not None:
            raise self.failure


def build_runtime(agent, channel=None):
    channel = channel or AgentEventChannel()
    return StrandsAgentRuntime(agent, browser=None, context=None,
                               sensitive_inputs=None, channel=channel)


async def collect(runtime, agent_input):
    items = []
    async for item in runtime.stream(agent_input):
        items.append(item)
    return items


class StrandsRuntimeStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_deltas_become_message_items(self) -> None:
        agent = ScriptedAgent([{"data": "Looking"}, {"data": " at the page"}])
        runtime = build_runtime(agent)

        items = await collect(runtime, "do the thing")

        self.assertEqual(
            items,
            [
                {"kind": "message", "text": "Looking"},
                {"kind": "message", "text": " at the page"},
            ],
        )
        self.assertEqual(agent.inputs, ["do the thing"])

    async def test_an_interrupt_is_reported_and_its_id_remembered(self) -> None:
        interrupt = SimpleNamespace(id="v1:tool_call:abc:123", name="browser-mfa-code")
        agent = ScriptedAgent([{"result": build_result("interrupt", [interrupt])}])
        runtime = build_runtime(agent)

        items = await collect(runtime, "sign in")

        self.assertEqual(items, [{"kind": "interrupt"}])
        self.assertEqual(runtime.pending_interrupt_id, "v1:tool_call:abc:123")

    async def test_finishing_normally_clears_the_pending_interrupt(self) -> None:
        agent = ScriptedAgent([{"result": build_result("end_turn", text="All done")}])
        runtime = build_runtime(agent)
        runtime.pending_interrupt_id = "stale"

        items = await collect(runtime, "carry on")

        self.assertEqual(items, [])
        self.assertIsNone(runtime.pending_interrupt_id)
        self.assertEqual(await runtime.final_output(), "All done")

    async def test_narration_does_not_leak_into_the_final_output(self) -> None:
        agent = ScriptedAgent(
            [
                {"data": "Let me open the page."},
                {"data": "Good, I can see it."},
                {"result": build_result("end_turn", text="The code is HOME-7F3.")},
            ]
        )
        runtime = build_runtime(agent)

        items = await collect(runtime, "read the page code")

        self.assertEqual(len(items), 2)
        self.assertEqual(await runtime.final_output(), "The code is HOME-7F3.")

    async def test_tool_events_reach_the_stream_in_order(self) -> None:
        channel = AgentEventChannel()
        agent = ScriptedAgent(
            [
                lambda: channel.publish("browser.action.started", {"action": "click"}),
                {"data": "clicked"},
            ]
        )
        runtime = build_runtime(agent, channel)

        items = await collect(runtime, "click it")

        self.assertEqual(
            items,
            [
                {
                    "kind": "custom",
                    "event_type": "browser.action.started",
                    "payload": {"action": "click"},
                },
                {"kind": "message", "text": "clicked"},
            ],
        )

    async def test_a_failing_agent_loop_surfaces_its_error(self) -> None:
        agent = ScriptedAgent([{"data": "half way"}], failure=RuntimeError("model died"))
        runtime = build_runtime(agent)

        with self.assertRaisesRegex(RuntimeError, "model died"):
            await collect(runtime, "do the thing")

    async def test_a_turn_that_ends_early_does_not_poison_the_next_one(self) -> None:
        channel = AgentEventChannel()
        first = ScriptedAgent(
            [
                {"result": build_result("interrupt", [SimpleNamespace(id="i-1")])},
                {"data": "never read"},
            ]
        )
        runtime = build_runtime(first, channel)

        async for item in runtime.stream("start"):
            if item["kind"] == "interrupt":
                break

        runtime.agent = ScriptedAgent([{"data": "resumed"}])
        items = await collect(runtime, runtime.resume_input("go on"))

        self.assertEqual(items, [{"kind": "message", "text": "resumed"}])


class StrandsRuntimeResumeTests(unittest.TestCase):
    def test_resume_input_carries_the_interrupt_id(self) -> None:
        runtime = build_runtime(ScriptedAgent([]))
        runtime.pending_interrupt_id = "v1:tool_call:abc:123"

        resume = runtime.resume_input({"entered": True})

        self.assertEqual(
            resume,
            [
                {
                    "interruptResponse": {
                        "interruptId": "v1:tool_call:abc:123",
                        "response": {"entered": True},
                    }
                }
            ],
        )

    def test_resuming_without_a_pending_interrupt_is_refused(self) -> None:
        runtime = build_runtime(ScriptedAgent([]))

        with self.assertRaisesRegex(RuntimeError, "not waiting"):
            runtime.resume_input("go on")


class ExtractTextTests(unittest.TestCase):
    def test_plain_text_is_returned_as_is(self) -> None:
        self.assertEqual(extract_text("Task complete"), "Task complete")

    def test_text_blocks_are_joined(self) -> None:
        content = [{"text": "Page code is "}, {"text": "HOME-7F3"}]

        self.assertEqual(extract_text(content), "Page code is HOME-7F3")

    def test_blocks_without_text_are_skipped(self) -> None:
        content = [{"toolUse": {"name": "observe_page"}}, {"text": "done"}]

        self.assertEqual(extract_text(content), "done")

    def test_anything_else_is_empty(self) -> None:
        self.assertEqual(extract_text(None), "")
        self.assertEqual(extract_text({"text": "not a list"}), "")


if __name__ == "__main__":
    unittest.main()
