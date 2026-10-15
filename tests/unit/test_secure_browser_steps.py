import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, Interrupt
from pydantic import ValidationError

from tabvio.agents.browser_agent.context import AgentContext
from tabvio.agents.browser_agent.schema_utils import SchemaUtils
from tabvio.agents.browser_agent.steps import StepPlan, validate_plan
from tabvio.agents.browser_agent.tools import build_browser_tools
from tabvio.browser.models import Element
from tabvio.browser.payment_detection_result import PaymentDetectionResult
from tabvio.runs.sensitive_input import SensitiveInputChannel


class ObservedBrowser:
    def __init__(self):
        self.elements = {
            1: Element(1, "user", "input", "Email", "type=email", 0, 0),
            2: Element(2, "pass", "input", "Password", "type=password", 0, 0),
            3: Element(3, "code", "input", "Code", "type=text", 0, 0),
            4: Element(4, "button", "button", "Sign in", "", 0, 0),
        }
        self.payment_detection_result = PaymentDetectionResult()
        self.fills = []
        self.clicks = []

    def get_stored_element(self, element_index):
        return self.elements.get(element_index)

    async def fill(self, element_index, value):
        self.fills.append((element_index, value))

    async def click(self, element_index):
        self.clicks.append(element_index)


class SecureBrowserStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._browser = ObservedBrowser()

    def test_credential_step_contains_reference_but_no_secret_values(self) -> None:
        credential_id = uuid4()
        plan = StepPlan.model_validate(
            {
                "steps": [
                    {
                        "action": "fill_credential",
                        "credential_id": str(credential_id),
                        "field": "login",
                        "element_index": 1,
                    },
                    {
                        "action": "fill_credential",
                        "credential_id": str(credential_id),
                        "field": "password",
                        "element_index": 2,
                    },
                    {"action": "click", "element_index": 4},
                ]
            }
        )

        validate_plan(self._browser, plan.steps)
        serialized = plan.model_dump_json()
        self.assertIn(str(credential_id), serialized)
        self.assertNotIn("password\":", serialized)
        self.assertNotIn("value\":", serialized)

    def test_a_code_step_may_name_the_control_that_sends_it(self) -> None:
        plan = StepPlan.model_validate(
            {
                "steps": [
                    {
                        "action": "request_mfa_code",
                        "element_index": 3,
                        "prompt": "Enter the code",
                        "submit_element_index": 4,
                    }
                ]
            }
        )

        validate_plan(self._browser, plan.steps)
        self.assertEqual(plan.steps[0].submit_element_index, 4)

    def test_a_code_step_cannot_submit_through_an_unobserved_element(self) -> None:
        plan = StepPlan.model_validate(
            {
                "steps": [
                    {
                        "action": "request_mfa_code",
                        "element_index": 3,
                        "prompt": "Enter the code",
                        "submit_element_index": 99,
                    }
                ]
            }
        )

        with self.assertRaises(ValueError) as rejection:
            validate_plan(self._browser, plan.steps)

        self.assertIn("[99]", str(rejection.exception))

    def test_a_code_step_without_a_named_control_still_validates(self) -> None:
        plan = StepPlan.model_validate(
            {
                "steps": [
                    {
                        "action": "request_mfa_code",
                        "element_index": 3,
                        "prompt": "Enter the code",
                    }
                ]
            }
        )

        validate_plan(self._browser, plan.steps)
        self.assertIsNone(plan.steps[0].submit_element_index)

    def test_credential_password_target_must_be_a_password_input(self) -> None:
        plan = StepPlan.model_validate(
            {
                "steps": [
                    {
                        "action": "fill_credential",
                        "credential_id": str(uuid4()),
                        "field": "password",
                        "element_index": 3,
                    }
                ]
            }
        )

        with self.assertRaisesRegex(ValueError, "type=password"):
            validate_plan(self._browser, plan.steps)

    def test_mfa_step_must_be_final_and_has_no_code_field(self) -> None:
        with self.assertRaises(ValidationError):
            StepPlan.model_validate(
                {
                    "steps": [
                        {
                            "action": "request_mfa_code",
                            "element_index": 3,
                            "prompt": "Enter the code",
                            "code": "123456",
                        }
                    ]
                }
            )

        plan = StepPlan.model_validate(
            {
                "steps": [
                    {
                        "action": "request_mfa_code",
                        "element_index": 3,
                        "prompt": "Enter the code",
                    },
                    {"action": "click", "element_index": 4},
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "must be final"):
            validate_plan(self._browser, plan.steps)


class StepPlanSchemaTests(unittest.TestCase):
    def test_schema_uses_strict_compatible_union_and_required_nullable_fields(self):
        original = StepPlan.model_json_schema()
        before = json.dumps(original)
        schema = SchemaUtils.inline_references(original)
        serialized = json.dumps(schema)
        for unsupported in ("$ref", "$defs", "discriminator", "oneOf", "default"):
            self.assertNotIn(unsupported, serialized)
        self.assertEqual(json.dumps(original), before)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["steps"])
        actions = []
        for branch in schema["properties"]["steps"]["items"]["anyOf"]:
            actions.append(branch["properties"]["action"]["const"])
            self.assertFalse(branch["additionalProperties"])
            self.assertEqual(branch["required"], list(branch["properties"]))
            if branch["properties"]["action"]["const"] == "request_mfa_code":
                self.assertIn({"type": "null"}, branch["properties"]["submit_element_index"]["anyOf"])
        self.assertEqual(sorted(actions), [
            "click", "fill", "fill_credential", "press", "request_mfa_code", "select",
        ])

    def test_runtime_is_not_exposed_in_any_browser_tool_schema(self):
        for browser_tool in build_browser_tools(ObservedBrowser()):
            schema = convert_to_openai_tool(browser_tool, strict=True)["function"]["parameters"]
            self.assertNotIn("runtime", schema["properties"])
            if browser_tool.name == "execute_steps":
                self.assertEqual(list(schema["properties"]), ["steps"])
                self.assertIn("anyOf", schema["properties"]["steps"]["items"])


class BrowserToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.browser = ObservedBrowser()
        self.sensitive_inputs = SensitiveInputChannel()
        self.tools = {}
        for browser_tool in build_browser_tools(self.browser, sensitive_inputs=self.sensitive_inputs):
            self.tools[browser_tool.name] = browser_tool
        self.events = []
        event_patch = patch(
            "tabvio.agents.browser_agent.tools.publish_custom_event",
            side_effect=lambda event_type, payload: self.events.append(
                {"event_type": event_type, "payload": payload}
            ),
        )
        event_patch.start()
        self.addCleanup(event_patch.stop)
        self.runtime = ToolRuntime(
            state={"messages": []}, context=AgentContext(user_id=None), config={},
            stream_writer=lambda item: None, tool_call_id=str(uuid4()), store=None,
        )

    async def test_invalid_steps_are_reported_without_acting(self):
        invalid_plans = [
            [],
            [{"action": "teleport", "element_index": 1}],
            [{"action": "click", "element_index": 99}],
            [{"action": "fill", "element_index": 4, "value": "hello"}],
            [{"action": "click", "element_index": 4}, {"action": "fill", "element_index": 1, "value": "hello"}],
        ]
        for steps in invalid_plans:
            with self.subTest(steps=steps):
                result = json.loads(await self.tools["execute_steps"].coroutine(steps, self.runtime))
                self.assertEqual(result["kind"], "validation_error")
                self.assertEqual(result["completed"], [])
        self.assertEqual(self.browser.fills, [])
        self.assertEqual(self.browser.clicks, [])
        self.assertEqual(self.events, [])

    async def test_browser_mutations_are_serialized(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def navigate(url):
            entered.set()
            await release.wait()
            return SimpleNamespace(page_state="Navigated")

        self.browser.attempt_navigate_and_observe = navigate
        self.browser.attempt_observe_page = AsyncMock(return_value=SimpleNamespace(page_state="Observed"))
        navigation = asyncio.create_task(self.tools["navigate_and_observe"].coroutine("https://shop.example", self.runtime))
        await entered.wait()
        observation = asyncio.create_task(self.tools["observe_page"].coroutine(self.runtime))
        await asyncio.sleep(0)
        self.browser.attempt_observe_page.assert_not_awaited()
        release.set()
        self.assertEqual(await asyncio.gather(navigation, observation), ["Navigated", "Observed"])

    async def test_select_and_press_use_browser_values_and_publish_action_events(self):
        self.browser.select = AsyncMock()
        self.browser.press = AsyncMock()
        result = json.loads(await self.tools["execute_steps"].coroutine([
            {"action": "select", "element_index": 1, "value": "standard"},
            {"action": "press", "element_index": 1, "value": "Enter"},
        ], self.runtime))

        self.assertTrue(result["ok"])
        self.assertEqual(result["completed"], ["select[1]", "press[1]"])
        self.browser.select.assert_awaited_once_with(1, "standard")
        self.browser.press.assert_awaited_once_with(1, "Enter")
        self.assertEqual(self.events, [
            {"event_type": "browser.action.started", "payload": {"action": "select", "target": "Email"}},
            {"event_type": "browser.action.completed", "payload": {"action": "select", "target": "Email"}},
            {"event_type": "browser.action.started", "payload": {"action": "press", "target": "Email"}},
            {"event_type": "browser.action.completed", "payload": {"action": "press", "target": "Email"}},
        ])

    async def test_failed_action_preserves_completed_steps_and_publishes_failure(self):
        self.browser.click = AsyncMock(side_effect=RuntimeError("Page changed"))
        result = json.loads(await self.tools["execute_steps"].coroutine([
            {"action": "fill", "element_index": 1, "value": "Ada"},
            {"action": "click", "element_index": 4},
        ], self.runtime))

        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "execution_error")
        self.assertEqual(result["completed"], ["fill[1]"])
        self.assertEqual(result["failed"], "click[4]")
        self.assertEqual(result["error"], "Page changed")
        self.assertEqual(self.browser.fills, [(1, "Ada")])
        self.assertEqual(self.events[-1], {
            "event_type": "browser.action.failed",
            "payload": {"action": "click", "target": "Sign in", "error": "Page changed"},
        })

    async def test_unsuccessful_mfa_resume_clears_request_and_reports_failure(self):
        with patch("tabvio.agents.browser_agent.tools.interrupt", return_value={"entered": False}):
            result = json.loads(await self.tools["execute_steps"].coroutine([{
                "action": "request_mfa_code", "element_index": 3,
                "prompt": "Enter the code", "submit_element_index": 4,
            }], self.runtime))

        self.assertEqual(result["kind"], "execution_error")
        self.assertEqual(result["failed"], "request_mfa_code[3]")
        self.assertIsNone(self.sensitive_inputs.pending)
        self.assertEqual(self.browser.fills, [])
        self.assertEqual(self.browser.clicks, [])
        self.assertEqual(self.events[1]["event_type"], "sensitive_input.required")
        self.assertEqual(self.events[-1]["event_type"], "browser.action.failed")

    def test_question_interrupt_keeps_the_frontend_payload(self):
        question = "Which delivery slot?"
        with patch("tabvio.agents.browser_agent.tools.interrupt", return_value="Tomorrow") as pause:
            answer = self.tools["request_user_input"].func(question, self.runtime)
        self.assertEqual(answer, "Tomorrow")
        pause.assert_called_once_with({"kind": "question", "question": question})
        self.assertEqual(self.events, [{"event_type": "input.required", "payload": {"question": question}}])

    async def test_mfa_interrupt_escapes_execution_and_resume_clears_request(self):
        steps = [{"action": "request_mfa_code", "element_index": 3, "prompt": "Enter the code", "submit_element_index": 4}]

        def pause(value):
            raise GraphInterrupt((Interrupt(value=value, id=str(uuid4())),))

        with patch("tabvio.agents.browser_agent.tools.interrupt", side_effect=pause):
            with self.assertRaises(GraphInterrupt) as raised:
                await self.tools["execute_steps"].coroutine(steps, self.runtime)
        request = self.sensitive_inputs.pending
        self.assertEqual(request.submit_element_index, 4)
        self.assertEqual(raised.exception.args[0][0].value, {"kind": "mfa_code", "request_id": str(request.id)})
        with patch("tabvio.agents.browser_agent.tools.interrupt", return_value={"entered": True}):
            result = json.loads(await self.tools["execute_steps"].coroutine(steps, self.runtime))
        self.assertTrue(result["ok"])
        self.assertIsNone(self.sensitive_inputs.pending)
        self.assertEqual(self.browser.fills, [])

    async def test_flattened_schema_executes_with_injected_runtime_in_a_graph(self):
        graph = StateGraph(MessagesState, context_schema=AgentContext)
        graph.add_node("tools", ToolNode(list(self.tools.values())))
        graph.set_entry_point("tools")
        graph.set_finish_point("tools")
        agent = graph.compile(checkpointer=InMemorySaver())
        result = await agent.ainvoke({"messages": [AIMessage("", tool_calls=[{
            "id": str(uuid4()), "name": "execute_steps", "args": {"steps": [
                {"action": "fill", "element_index": 1, "value": "Ada"},
                {"action": "click", "element_index": 4},
            ]},
        }])]}, {"configurable": {"thread_id": str(uuid4())}}, context=AgentContext(user_id=None))
        self.assertTrue(json.loads(result["messages"][-1].content)["ok"])
        self.assertEqual(self.browser.fills, [(1, "Ada")])
        self.assertEqual(self.browser.clicks, [4])

    async def test_mfa_pauses_and_resumes_through_a_checkpoint(self):
        graph = StateGraph(MessagesState, context_schema=AgentContext)
        graph.add_node("tools", ToolNode(list(self.tools.values())))
        graph.set_entry_point("tools")
        graph.set_finish_point("tools")
        agent = graph.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": str(uuid4())}}
        paused = await agent.ainvoke({"messages": [AIMessage("", tool_calls=[{
            "id": str(uuid4()), "name": "execute_steps", "args": {"steps": [{
                "action": "request_mfa_code", "element_index": 3,
                "prompt": "Enter the code", "submit_element_index": 4,
            }]},
        }])]}, config, context=AgentContext(user_id=None))
        request_id = str(self.sensitive_inputs.pending.id)
        self.assertEqual(paused["__interrupt__"][0].value, {"kind": "mfa_code", "request_id": request_id})
        resumed = await agent.ainvoke(Command(resume={"entered": True}), config)
        self.assertNotIn("__interrupt__", resumed)
        self.assertTrue(json.loads(resumed["messages"][-1].content)["ok"])
        self.assertIsNone(self.sensitive_inputs.pending)
        self.assertEqual(self.browser.fills, [])


if __name__ == "__main__":
    unittest.main()
