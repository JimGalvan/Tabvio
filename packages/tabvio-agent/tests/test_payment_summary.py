import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, Interrupt
from tabvio_agent.browser.models import PaymentSignal
from tabvio_agent.browser.payment_detection_result import PaymentDetectionResult
from tabvio_agent.browser_agent.context import AgentContext
from tabvio_agent.browser_agent.payment_summary import (
    generate_payment_summary,
    summary_evidence,
)
from tabvio_agent.browser_agent.tools import build_browser_tools
from tabvio_agent.settings import AgentSettings

SUMMARY_MODULE = "tabvio_agent.browser_agent.payment_summary"
SUMMARY_TEXT = "I added two items and selected standard delivery. The total is USD 48.50."
PAGE_STATE = "Checkout: two items. Total USD 48.50. Card number: [empty]"


def source_state():
    return {
        "messages": [
            HumanMessage("Buy two items with standard delivery"),
            AIMessage("", tool_calls=[{
                "name": "execute_steps", "id": "step-1",
                "args": {"steps": [{"action": "click", "element_index": 4}]},
            }]),
            ToolMessage('{"ok": true, "completed": ["click[4]"]}', tool_call_id="step-1"),
        ],
        "todos": [{"content": "Choose delivery", "status": "completed"}],
    }


class PaymentSummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = AgentSettings(fast_model="anthropic:summary-model")
        self.model_patch = patch(f"{SUMMARY_MODULE}.build_fast_model")
        self.model_factory = self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.invoke = AsyncMock(return_value=AIMessage(
            f" {SUMMARY_TEXT}\n", response_metadata={"finish_reason": "stop"}
        ))
        self.model_factory.return_value.ainvoke = self.invoke

    async def test_summary_uses_history_todos_and_current_page_without_tools(self):
        agent = source_state()
        before = list(agent["messages"])
        summary = await generate_payment_summary(agent, PAGE_STATE, self.settings)

        self.assertEqual(summary, SUMMARY_TEXT)
        self.model_factory.assert_called_once_with(self.settings.fast_model)
        self.model_factory.return_value.bind_tools.assert_not_called()
        self.assertEqual(agent["messages"], before)
        evidence = json.loads(self.invoke.call_args.args[0][1].content)
        self.assertIn("Buy two items", evidence["original_request"])
        self.assertIn("click[4]", evidence["recent_history_excerpt"])
        self.assertEqual(evidence["current_todos"], agent["todos"])
        self.assertEqual(evidence["current_page_snapshot"], PAGE_STATE)

    async def test_model_failure_and_empty_or_incomplete_results_return_no_summary(self):
        for outcome in (RuntimeError("provider failure"), "", "   ", "incomplete"):
            with self.subTest(outcome=outcome):
                if isinstance(outcome, Exception):
                    self.invoke.side_effect = outcome
                else:
                    self.invoke.side_effect = None
                    self.invoke.return_value = AIMessage(
                        outcome, response_metadata={
                            "finish_reason": "length" if outcome == "incomplete" else "stop"
                        },
                    )
                self.assertEqual(await generate_payment_summary(source_state(), PAGE_STATE, self.settings), "")

    async def test_timeout_cancels_summary_and_returns_no_summary(self):
        cancelled = asyncio.Event()

        async def stall(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        self.invoke.side_effect = stall
        with patch(f"{SUMMARY_MODULE}.SUMMARY_TIMEOUT_SECONDS", 0.01):
            self.assertEqual(await generate_payment_summary(source_state(), PAGE_STATE, self.settings), "")
        self.assertTrue(cancelled.is_set())

    async def test_run_cancellation_is_not_swallowed(self):
        self.invoke.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await generate_payment_summary(source_state(), PAGE_STATE, self.settings)

    async def test_incomplete_responses_result_is_not_shown(self):
        self.invoke.return_value = AIMessage(
            SUMMARY_TEXT, response_metadata={"status": "incomplete"}
        )
        self.assertEqual(await generate_payment_summary(source_state(), PAGE_STATE, self.settings), "")

    def test_long_history_keeps_request_and_recent_results_and_omits_images_and_reasoning(self):
        agent = source_state()
        agent["messages"].insert(1, HumanMessage(content=[
            {"type": "text", "text": "old observation" * 10_000},
            {"type": "image", "data": "image-data"},
            {"type": "reasoning", "text": "private reasoning"},
        ]))
        evidence = summary_evidence(agent, PAGE_STATE)
        parsed = json.loads(evidence)
        self.assertLessEqual(len(parsed["recent_history_excerpt"]), 60_000)
        self.assertIn("Buy two items", parsed["original_request"])
        self.assertIn("click[4]", parsed["recent_history_excerpt"])
        self.assertNotIn("image-data", evidence)
        self.assertNotIn("private reasoning", evidence)


class PaymentHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = AgentSettings(fast_model="anthropic:handoff-model")
        self.detection = PaymentDetectionResult(
            url="https://shop.example/pay",
            signals=(PaymentSignal(type="card-autocomplete", value="cc-number"),),
        )
        self.assertTrue(self.detection.needs_handoff(None))
        self.browser = SimpleNamespace(
            payment_detection_result=self.detection,
            attempt_navigate_and_observe=AsyncMock(return_value=SimpleNamespace(page_state=PAGE_STATE)),
            attempt_observe_page=AsyncMock(return_value=SimpleNamespace(page_state="Refreshed after payment")),
            switch_tab=AsyncMock(return_value=PAGE_STATE),
            switch_to_iframe=AsyncMock(return_value=PAGE_STATE),
        )
        self.events = []
        event_patch = patch(
            "tabvio_agent.browser_agent.tools.publish_custom_event",
            side_effect=lambda event_type, payload: self.events.append(
                {"event_type": event_type, "payload": payload}
            ),
        )
        event_patch.start()
        self.addCleanup(event_patch.stop)
        self.tools = {}
        for browser_tool in build_browser_tools(self.browser, settings=self.settings):
            self.tools[browser_tool.name] = browser_tool.coroutine
        self.runtime = SimpleNamespace(context=AgentContext(), state=source_state())
        interrupt_patch = patch(
            "tabvio_agent.browser_agent.tools.interrupt", side_effect=self.pause
        )
        self.interrupt = interrupt_patch.start()
        self.addCleanup(interrupt_patch.stop)

    @staticmethod
    def pause(value):
        raise GraphInterrupt((Interrupt(value=value, id="payment"),))

    def questions(self):
        questions = []
        for item in self.events:
            if item["event_type"] == "input.required":
                questions.append(item["payload"]["question"])
        self.events.clear()
        return questions

    async def test_summary_is_shown_before_pause_and_reused_on_resume_without_navigation(self):
        with patch(
            "tabvio_agent.browser_agent.tools.generate_payment_summary",
            new=AsyncMock(return_value=SUMMARY_TEXT),
        ) as summary:
            with self.assertRaises(GraphInterrupt) as raised:
                await self.tools["navigate_and_observe"]("https://shop.example/pay", self.runtime)
            payload = raised.exception.args[0][0].value
            self.assertEqual(payload["kind"], "payment_handoff")
            question = payload["question"]
            self.assertTrue(question.startswith(SUMMARY_TEXT + "\n\n"))
            self.assertIn("enter the payment details yourself", question)
            self.assertEqual(self.events[-1], {
                "event_type": "input.required",
                "payload": {"question": question, "payment_signals": self.detection.get_signals},
            })
            self.assertEqual(self.questions(), [question])
            self.browser.attempt_observe_page.assert_not_awaited()
            summary.assert_awaited_once_with(self.runtime.state, PAGE_STATE, self.settings)

            self.interrupt.side_effect = None
            result = await self.tools["navigate_and_observe"]("https://shop.example/pay", self.runtime)
            self.assertEqual(result, "Refreshed after payment")
            self.browser.attempt_navigate_and_observe.assert_awaited_once()
            self.browser.attempt_observe_page.assert_awaited_once()
            summary.assert_awaited_once()
            self.assertEqual(self.questions(), [question])

            await self.tools["observe_page"](self.runtime)
            summary.assert_awaited_once()
            self.assertEqual(self.questions(), [])

            self.browser.payment_detection_result = PaymentDetectionResult(
                url="https://another-shop.example/pay", signals=self.detection.signals
            )
            self.interrupt.side_effect = self.pause
            with self.assertRaises(GraphInterrupt):
                await self.tools["observe_page"](self.runtime)
            self.assertEqual(summary.await_count, 2)

    async def test_model_failure_still_publishes_the_original_handoff_and_interrupts(self):
        with patch(f"{SUMMARY_MODULE}.build_fast_model", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(GraphInterrupt):
                await self.tools["switch_tab"]("tab:2", self.runtime)
        questions = self.questions()
        self.assertEqual(len(questions), 1)
        self.assertTrue(questions[0].startswith("This page can take a payment"))
        self.browser.attempt_observe_page.assert_not_awaited()

    async def test_payment_guard_interrupts_before_validating_or_executing_steps(self):
        self.browser.get_stored_element = AsyncMock()
        self.browser.click = AsyncMock()
        self.browser.fill = AsyncMock()
        with patch(
            "tabvio_agent.browser_agent.tools.generate_payment_summary",
            new=AsyncMock(return_value=SUMMARY_TEXT),
        ) as summary:
            with self.assertRaises(GraphInterrupt):
                await self.tools["execute_steps"]([
                    {"action": "click", "element_index": 4},
                ], self.runtime)

        summary.assert_awaited_once_with(self.runtime.state, "", self.settings)
        self.browser.get_stored_element.assert_not_called()
        self.browser.click.assert_not_awaited()
        self.browser.fill.assert_not_awaited()
        self.assertEqual(self.events[0]["event_type"], "input.required")

    async def test_observe_and_switch_replay_refresh_without_repeating_browser_calls(self):
        for name, args in (
            ("observe_page", ()), ("switch_tab", ("tab:2",)), ("switch_frame", ("frame:1",)),
        ):
            with self.subTest(tool=name):
                self.events.clear()
                self.browser.attempt_observe_page.reset_mock()
                self.browser.switch_tab.reset_mock()
                self.browser.switch_to_iframe.reset_mock()
                self.interrupt.side_effect = self.pause
                tools = {}
                for browser_tool in build_browser_tools(self.browser):
                    tools[browser_tool.name] = browser_tool.coroutine
                with patch(
                    "tabvio_agent.browser_agent.tools.generate_payment_summary",
                    new=AsyncMock(return_value=SUMMARY_TEXT),
                ) as summary:
                    with self.assertRaises(GraphInterrupt):
                        await tools[name](*args, self.runtime)
                    first_event = self.events[0]
                    self.interrupt.side_effect = None
                    result = await tools[name](*args, self.runtime)

                self.assertEqual(result, "Refreshed after payment")
                summary.assert_awaited_once()
                if name == "observe_page":
                    self.assertEqual(first_event["event_type"], "browser.observation")
                    self.assertEqual(self.browser.attempt_observe_page.await_count, 2)
                    self.browser.switch_tab.assert_not_awaited()
                elif name == "switch_tab":
                    self.assertEqual(first_event, {
                        "event_type": "browser.tab.changed", "payload": {"tab_id": "tab:2"},
                    })
                    self.browser.switch_tab.assert_awaited_once_with("tab:2")
                    self.browser.attempt_observe_page.assert_awaited_once()
                else:
                    self.assertEqual(first_event, {
                        "event_type": "browser.frame.changed", "payload": {"frame_id": "frame:1"},
                    })
                    self.browser.switch_to_iframe.assert_awaited_once_with("frame:1")
                    self.browser.attempt_observe_page.assert_awaited_once()
                    changed_events = []
                    for event in self.events:
                        if event["event_type"] == "browser.frame.changed":
                            changed_events.append(event)
                    self.assertEqual(changed_events, [first_event])


class PaymentGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_runtime_injection_and_payment_resume_through_a_checkpoint(self):
        browser = SimpleNamespace(
            payment_detection_result=PaymentDetectionResult(
                url="https://shop.example/pay",
                signals=(PaymentSignal(type="card-autocomplete", value="cc-number"),),
            ),
            attempt_navigate_and_observe=AsyncMock(return_value=SimpleNamespace(page_state=PAGE_STATE)),
            attempt_observe_page=AsyncMock(return_value=SimpleNamespace(page_state="Payment complete")),
        )
        graph = StateGraph(MessagesState, context_schema=AgentContext)
        graph.add_node("tools", ToolNode(build_browser_tools(browser)))
        graph.set_entry_point("tools")
        graph.set_finish_point("tools")
        agent = graph.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": str(uuid4())}}
        call_id = str(uuid4())
        with patch(
            "tabvio_agent.browser_agent.tools.generate_payment_summary",
            new=AsyncMock(return_value=SUMMARY_TEXT),
        ) as summary:
            paused = await agent.ainvoke({"messages": [
                HumanMessage("Buy two items with standard delivery"),
                AIMessage("", tool_calls=[{
                    "id": call_id, "name": "navigate_and_observe",
                    "args": {"url": "https://shop.example/pay"},
                }]),
            ]}, config, context=AgentContext())
            payload = paused["__interrupt__"][0].value
            self.assertEqual(payload["kind"], "payment_handoff")
            self.assertTrue(payload["question"].startswith(SUMMARY_TEXT))
            resumed = await agent.ainvoke(Command(resume="Continue"), config)
        self.assertNotIn("__interrupt__", resumed)
        self.assertEqual(resumed["messages"][-1].content, "Payment complete")
        self.assertEqual(resumed["messages"][-1].tool_call_id, call_id)
        browser.attempt_navigate_and_observe.assert_awaited_once()
        browser.attempt_observe_page.assert_awaited_once()
        summary.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
