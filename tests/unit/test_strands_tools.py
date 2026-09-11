import json
import unittest
from uuid import uuid4

from tabvio.agents.browser_agent.context import AgentContext
from tabvio.agents.strands.events import AgentEventChannel
from tabvio.agents.strands.tools import STEP_PLAN_SCHEMA, build_browser_tools
from tabvio.browser.constants import PAYMENT_HANDOFF_SIGNAL_KINDS
from tabvio.browser.models import Element, PaymentSignal
from tabvio.browser.payment_detection_result import PaymentDetectionResult


class ObservedBrowser:
    def __init__(self, payment_detection_result=None):
        self.elements = {
            1: Element(1, "user", "input", "Email", "type=email", 0, 0),
            2: Element(2, "pass", "input", "Password", "type=password", 0, 0),
            4: Element(4, "button", "button", "Sign in", "", 0, 0),
        }
        self.fills = []
        self.clicks = []
        self.payment_detection_result = payment_detection_result or PaymentDetectionResult()

    def get_stored_element(self, element_index):
        return self.elements.get(element_index)

    async def fill(self, element_index, value):
        self.fills.append((element_index, value))

    async def click(self, element_index):
        self.clicks.append(element_index)


class RecordingToolContext:
    def __init__(self, responses=None):
        self.raised = []
        self._responses = responses or {}

    def interrupt(self, name, reason=None):
        self.raised.append((name, reason))
        return self._responses.get(name)


def build_payment_page():
    signal_kind = sorted(PAYMENT_HANDOFF_SIGNAL_KINDS)[0]
    return PaymentDetectionResult(
        url="https://shop.example.com/pay",
        signals=(PaymentSignal(type=signal_kind, value="cc-number"),),
    )


def build_tools(browser, agent_context=None, channel=None):
    channel = channel or AgentEventChannel()
    agent_context = agent_context or AgentContext(user_id=None)
    tools = build_browser_tools(browser, channel, agent_context)
    return {tool.tool_name: tool._tool_func for tool in tools}, channel


class StepPlanSchemaTests(unittest.TestCase):
    def test_the_schema_stands_alone(self) -> None:
        serialized = json.dumps(STEP_PLAN_SCHEMA)

        self.assertNotIn("$ref", serialized)
        self.assertNotIn("$defs", serialized)
        self.assertNotIn("discriminator", serialized)

    def test_every_step_type_survives_flattening(self) -> None:
        branches = STEP_PLAN_SCHEMA["properties"]["steps"]["items"]["oneOf"]

        actions = []
        for branch in branches:
            actions.append(branch["properties"]["action"]["const"])

        self.assertEqual(
            sorted(actions),
            [
                "click",
                "fill",
                "fill_credential",
                "press",
                "request_mfa_code",
                "select",
            ],
        )

    def test_the_tool_context_is_not_offered_to_the_model(self) -> None:
        self.assertEqual(list(STEP_PLAN_SCHEMA["properties"]), ["steps"])
        self.assertNotIn("tool_context", json.dumps(STEP_PLAN_SCHEMA))


class ExecuteStepsTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_bad_plan_comes_back_as_a_validation_error(self) -> None:
        tools, _ = build_tools(ObservedBrowser())

        result = json.loads(
            await tools["execute_steps"](
                steps=[{"action": "click", "element_index": 99}],
                tool_context=RecordingToolContext(),
            )
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "validation_error")
        self.assertIn("[99]", result["error"])

    async def test_an_unknown_action_is_reported_rather_than_raised(self) -> None:
        tools, _ = build_tools(ObservedBrowser())

        result = json.loads(
            await tools["execute_steps"](
                steps=[{"action": "teleport", "element_index": 1}],
                tool_context=RecordingToolContext(),
            )
        )

        self.assertEqual(result["kind"], "validation_error")

    async def test_a_good_plan_runs_and_reports_each_step(self) -> None:
        browser = ObservedBrowser()
        tools, channel = build_tools(browser)

        result = json.loads(
            await tools["execute_steps"](
                steps=[
                    {"action": "fill", "element_index": 1, "value": "ada@example.com"},
                    {"action": "click", "element_index": 4},
                ],
                tool_context=RecordingToolContext(),
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["completed"], ["fill[1]", "click[4]"])
        self.assertEqual(browser.fills, [(1, "ada@example.com")])
        self.assertEqual(browser.clicks, [4])

    async def test_a_credential_not_chosen_for_the_run_is_refused(self) -> None:
        browser = ObservedBrowser()
        tools, _ = build_tools(
            browser, AgentContext(user_id=uuid4(), credential_ids=(uuid4(),))
        )

        result = json.loads(
            await tools["execute_steps"](
                steps=[
                    {
                        "action": "fill_credential",
                        "credential_id": str(uuid4()),
                        "field": "login",
                        "element_index": 1,
                    }
                ],
                tool_context=RecordingToolContext(),
            )
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "execution_error")
        self.assertEqual(browser.fills, [])

    async def test_a_payment_page_stops_the_plan_before_anything_runs(self) -> None:
        browser = ObservedBrowser(build_payment_page())
        tools, _ = build_tools(browser)
        tool_context = RecordingToolContext()

        await tools["execute_steps"](
            steps=[{"action": "click", "element_index": 4}],
            tool_context=tool_context,
        )

        raised_names = [name for name, _ in tool_context.raised]
        self.assertIn("browser-payment-handoff", raised_names)


class RequestUserInputTests(unittest.TestCase):
    def test_the_question_is_published_and_the_answer_returned(self) -> None:
        tools, channel = build_tools(ObservedBrowser())
        tool_context = RecordingToolContext({"browser-question": "size ten"})

        answer = tools["request_user_input"](
            question="What size?", tool_context=tool_context
        )

        self.assertEqual(answer, "size ten")
        self.assertEqual(tool_context.raised[0][0], "browser-question")


if __name__ == "__main__":
    unittest.main()
