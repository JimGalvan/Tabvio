import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from strands.interrupt import Interrupt, InterruptException

from tabvio.agents.strands.browser_agent.context import AgentContext
from tabvio.agents.strands.browser_agent.payment_summary import (
    generate_payment_summary,
    summary_evidence,
)
from tabvio.agents.strands.browser_agent.tools import build_browser_tools
from tabvio.agents.strands.shared.events import AgentEventChannel
from tabvio.browser.models import PaymentSignal
from tabvio.browser.payment_detection_result import PaymentDetectionResult

SUMMARY_MODULE = "tabvio.agents.strands.browser_agent.payment_summary"
SUMMARY_TEXT = "I added two items and selected standard delivery. The total is USD 48.50."
PAGE_STATE = "Checkout: two items. Total USD 48.50. Card number: [empty]"


def source_agent():
    return SimpleNamespace(
        messages=[
            {"role": "user", "content": [{"text": "Buy two items with standard delivery"}]},
            {"role": "assistant", "content": [{"toolUse": {
                "name": "execute_steps", "toolUseId": "step-1",
                "input": {"steps": [{"action": "click", "element_index": 4}]},
            }}]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": "step-1", "status": "success",
                "content": [{"text": '{"ok": true, "completed": ["click[4]"]}'}],
            }}]},
        ],
        state={"todos": [{"content": "Choose delivery", "status": "completed"}]},
    )


class PaymentSummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.model_patch = patch(f"{SUMMARY_MODULE}.build_fast_model")
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.agent_patch = patch(f"{SUMMARY_MODULE}.Agent")
        self.agent_factory = self.agent_patch.start()
        self.addCleanup(self.agent_patch.stop)
        result = MagicMock(stop_reason="end_turn")
        result.__str__.return_value = f" {SUMMARY_TEXT}\n"
        self.invoke = AsyncMock(return_value=result)
        self.agent_factory.return_value.invoke_async = self.invoke

    async def test_summary_uses_history_todos_and_current_page_without_tools(self):
        agent = source_agent()
        before = json.dumps(agent.messages)
        summary = await generate_payment_summary(agent, PAGE_STATE)

        self.assertEqual(summary, SUMMARY_TEXT)
        self.assertEqual(self.agent_factory.call_args.kwargs["tools"], [])
        self.assertEqual(json.dumps(agent.messages), before)
        evidence = json.loads(self.invoke.call_args.args[0])
        self.assertIn("Buy two items", evidence["original_request"])
        self.assertIn("click[4]", evidence["recent_history_excerpt"])
        self.assertIn("[x] Choose delivery", evidence["current_todos"])
        self.assertEqual(evidence["current_page_snapshot"], PAGE_STATE)

    async def test_model_failure_and_empty_or_incomplete_results_return_no_summary(self):
        for outcome in (RuntimeError("provider failure"), "", "   ", "incomplete"):
            with self.subTest(outcome=outcome):
                if isinstance(outcome, Exception):
                    self.invoke.side_effect = outcome
                else:
                    self.invoke.side_effect = None
                    result = MagicMock(
                        stop_reason="max_tokens" if outcome == "incomplete" else "end_turn"
                    )
                    result.__str__.return_value = outcome
                    self.invoke.return_value = result
                self.assertEqual(await generate_payment_summary(source_agent(), PAGE_STATE), "")

    async def test_timeout_cancels_summary_and_returns_no_summary(self):
        cancelled = asyncio.Event()

        async def stall(_):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        self.invoke.side_effect = stall
        with patch(f"{SUMMARY_MODULE}.SUMMARY_TIMEOUT_SECONDS", 0.01):
            self.assertEqual(await generate_payment_summary(source_agent(), PAGE_STATE), "")
        self.assertTrue(cancelled.is_set())

    async def test_run_cancellation_is_not_swallowed(self):
        self.invoke.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await generate_payment_summary(source_agent(), PAGE_STATE)

    def test_long_history_keeps_request_and_recent_results_and_omits_images_and_reasoning(self):
        agent = source_agent()
        agent.messages.insert(1, {"role": "user", "content": [
            {"text": "old observation" * 10_000},
            {"image": {"source": {"bytes": b"image-data"}}},
            {"reasoningContent": {"text": "private reasoning"}},
        ]})
        evidence = summary_evidence(agent, PAGE_STATE)
        parsed = json.loads(evidence)
        self.assertLessEqual(len(parsed["recent_history_excerpt"]), 60_000)
        self.assertIn("Buy two items", parsed["original_request"])
        self.assertIn("click[4]", parsed["recent_history_excerpt"])
        self.assertNotIn("image-data", evidence)
        self.assertNotIn("private reasoning", evidence)


class PaymentHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
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
        )
        self.channel = AgentEventChannel()
        self.tools = {
            tool.tool_name: tool._tool_func
            for tool in build_browser_tools(self.browser, self.channel, AgentContext(user_id=None))
        }
        self.context = SimpleNamespace(agent=source_agent(), interrupt=MagicMock(side_effect=self.pause))

    @staticmethod
    def pause(name, reason):
        raise InterruptException(Interrupt(id="payment", name=name, reason=reason))

    async def questions(self):
        questions = []
        self.channel.finish()
        while (item := await self.channel.next_item())["kind"] != "finished":
            if item.get("event_type") == "input.required":
                questions.append(item["payload"]["question"])
        return questions

    async def test_summary_is_shown_before_pause_and_reused_on_resume_without_navigation(self):
        with patch(
            "tabvio.agents.strands.browser_agent.tools.generate_payment_summary",
            new=AsyncMock(return_value=SUMMARY_TEXT),
        ) as summary:
            with self.assertRaises(InterruptException) as raised:
                await self.tools["navigate_and_observe"]("https://shop.example/pay", self.context)
            question = raised.exception.interrupt.reason["question"]
            self.assertTrue(question.startswith(SUMMARY_TEXT + "\n\n"))
            self.assertIn("enter the payment details yourself", question)
            self.assertEqual(await self.questions(), [question])
            self.browser.attempt_observe_page.assert_not_awaited()
            summary.assert_awaited_once_with(self.context.agent, PAGE_STATE)

            self.context.interrupt.side_effect = None
            result = await self.tools["navigate_and_observe"]("https://shop.example/pay", self.context)
            self.assertEqual(result, "Refreshed after payment")
            self.browser.attempt_navigate_and_observe.assert_awaited_once()
            self.browser.attempt_observe_page.assert_awaited_once()
            summary.assert_awaited_once()
            self.assertEqual(await self.questions(), [question])

            await self.tools["observe_page"](self.context)
            summary.assert_awaited_once()
            self.assertEqual(await self.questions(), [])

            self.browser.payment_detection_result = PaymentDetectionResult(
                url="https://another-shop.example/pay", signals=self.detection.signals
            )
            self.context.interrupt.side_effect = self.pause
            with self.assertRaises(InterruptException):
                await self.tools["observe_page"](self.context)
            self.assertEqual(summary.await_count, 2)

    async def test_model_failure_still_publishes_the_original_handoff_and_interrupts(self):
        with patch(f"{SUMMARY_MODULE}.build_fast_model", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(InterruptException):
                await self.tools["switch_tab"]("tab:2", self.context)
        questions = await self.questions()
        self.assertEqual(len(questions), 1)
        self.assertTrue(questions[0].startswith("This page can take a payment"))
        self.browser.attempt_observe_page.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
