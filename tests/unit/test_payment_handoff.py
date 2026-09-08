"""Payment observation must pause even when no further action is requested."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from tabvio.agents.browser_agent.tools import build_browser_tools
from tabvio.browser.models import PaymentSignal
from tabvio.browser.payment_detection_result import PaymentDetectionResult


class PaymentHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_tools_pause_and_resume_without_repeating_navigation(self):
        for name, args in (
            ("navigate_and_observe", {"url": "https://shop.example/checkout"}),
            ("observe_page", {}),
            ("switch_tab", {"tab_id": "tab:2"}),
        ):
            with self.subTest(tool=name):
                payment = PaymentDetectionResult(
                    "https://shop.example/checkout",
                    (PaymentSignal(type="card-autocomplete", value="cc-number"),),
                )
                browser = SimpleNamespace(
                    payment_detection_result=payment,
                    attempt_navigate_and_observe=AsyncMock(return_value=SimpleNamespace(page_state="Payment")),
                    attempt_observe_page=AsyncMock(return_value=SimpleNamespace(page_state="Payment")),
                    switch_tab=AsyncMock(return_value="Payment"),
                )
                loader = SimpleNamespace(ainvoke=AsyncMock(return_value={
                    "messages": [SimpleNamespace(content="true")],
                }))
                with patch("tabvio.agents.browser_agent.tools.build_page_loader_detector_subagent", return_value=loader), patch("tabvio.agents.browser_agent.tools.time.sleep"):
                    tool = {t.name: t for t in build_browser_tools(browser)}[name]

                    async def observe(state):
                        return {"result": await tool.ainvoke(args)}

                    builder = StateGraph(dict)
                    builder.add_node("observe", observe)
                    builder.add_edge(START, "observe")
                    builder.add_edge("observe", END)
                    graph = builder.compile(checkpointer=InMemorySaver())
                    config = {"configurable": {"thread_id": str(uuid4())}}
                    paused = await graph.ainvoke({}, config)
                    self.assertEqual(paused["__interrupt__"][0].value["kind"], "payment_handoff")

                    # The person completes payment and moves to another page.
                    browser.payment_detection_result = PaymentDetectionResult()
                    browser.attempt_observe_page.return_value = SimpleNamespace(page_state="Order confirmed")
                    resumed = await graph.ainvoke(Command(resume="Continue"), config)
                    self.assertEqual(resumed["result"], "Order confirmed")
                    self.assertNotIn("__interrupt__", resumed)
                    self.assertEqual(browser.attempt_navigate_and_observe.await_count, int(name == "navigate_and_observe"))
                    self.assertEqual(browser.switch_tab.await_count, int(name == "switch_tab"))
                    self.assertEqual(browser.attempt_observe_page.await_count, 1 + int(name == "observe_page"))

