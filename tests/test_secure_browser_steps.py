import unittest
from uuid import uuid4

from pydantic import ValidationError

from tabvio.browser.models import Element
from tabvio.agents.browser_agent.steps import StepPlan, validate_plan
from tabvio.agents.browser_agent.tools import build_browser_tools


class ObservedBrowser:
    def __init__(self):
        self.elements = {
            1: Element(1, "user", "input", "Email", "type=email", 0, 0),
            2: Element(2, "pass", "input", "Password", "type=password", 0, 0),
            3: Element(3, "code", "input", "Code", "type=text", 0, 0),
            4: Element(4, "button", "button", "Sign in", "", 0, 0),
        }

    def get_stored_element(self, element_index):
        return self.elements.get(element_index)


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
                        "username_element_index": 1,
                        "password_element_index": 2,
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

    def test_runtime_context_is_not_exposed_in_tool_schema(self) -> None:
        tools = {tool.name: tool for tool in build_browser_tools(self._browser)}
        schema = tools["execute_steps"].args_schema.model_json_schema()

        self.assertEqual(list(schema["properties"]), ["steps"])
        self.assertNotIn("runtime", str(schema).lower())

    def test_credential_password_target_must_be_a_password_input(self) -> None:
        plan = StepPlan.model_validate(
            {
                "steps": [
                    {
                        "action": "fill_credential",
                        "credential_id": str(uuid4()),
                        "username_element_index": 1,
                        "password_element_index": 3,
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


if __name__ == "__main__":
    unittest.main()
