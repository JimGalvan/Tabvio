import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from tabvio_agent.browser.models import Element
from tabvio_agent.browser.payment_detection_result import PaymentDetectionResult
from tabvio_agent.browser_agent.context import AgentContext
from tabvio_agent.browser_agent.tools import build_browser_tools
from tabvio_agent.models import CredentialMetadata, CredentialSecret


class ObservedBrowser:
    def __init__(self):
        self.elements = {
            1: Element(1, "user", "input", "Email", "type=email", 0, 0),
            2: Element(2, "pass", "input", "Password", "type=password", 0, 0),
        }
        self.fills = []
        self.payment_detection_result = PaymentDetectionResult()

    def get_stored_element(self, element_index):
        return self.elements.get(element_index)

    async def fill(self, element_index, value):
        self.fills.append((element_index, value))


class InMemoryCredentialProvider:
    def __init__(self, metadata, secrets):
        self.metadata = metadata
        self.secrets = secrets
        self.resolve_error = None
        self.resolutions = []

    async def list_selected(self):
        return self.metadata

    async def resolve(self, credential_id, domain):
        self.resolutions.append((credential_id, domain))
        if self.resolve_error:
            raise self.resolve_error
        if credential_id not in self.secrets:
            raise ValueError("Credential is not selected for this run")
        return self.secrets[credential_id]


class AgentContactCredentialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.contact = {
            "email": "ada@example.com", "first_name": "Ada",
            "last_name": "Lovelace", "phone": "+16195550123",
        }
        self.metadata = CredentialMetadata(
            id=uuid4(), name="Checkout contact", allowed_domains=("shop.example",),
            login_hint="", is_default=False, available_fields=tuple(self.contact),
            preferred_verification=("sms", "email"),
        )
        self.provider = InMemoryCredentialProvider(
            (self.metadata,), {self.metadata.id: CredentialSecret(**self.contact)},
        )

    def browser_tools(self, provider=True, hostname="shop.example"):
        browser = ObservedBrowser()
        browser.current_hostname = hostname
        runtime = SimpleNamespace(context=AgentContext(), state={"messages": []})
        events = []
        event_patch = patch(
            "tabvio_agent.browser_agent.tools.publish_custom_event",
            side_effect=lambda event_type, payload: events.append(
                {"event_type": event_type, "payload": payload}
            ),
        )
        event_patch.start()
        self.addCleanup(event_patch.stop)
        tools = {}
        for browser_tool in build_browser_tools(browser, self.provider if provider else None):
            tools[browser_tool.name] = browser_tool.coroutine
        return tools, browser, events, runtime

    async def fill(self, tools, runtime, field):
        return json.loads(await tools["execute_steps"](steps=[{
            "action": "fill_credential", "credential_id": str(self.metadata.id),
            "field": field, "element_index": 2 if field == "password" else 1,
        }], runtime=runtime))

    async def test_contact_fields_fill_directly_without_values_in_tool_results_or_events(self):
        tools, browser, events, runtime = self.browser_tools()
        metadata = await tools["list_selected_credentials"](runtime)
        self.assertEqual(metadata, json.dumps([{
            "id": str(self.metadata.id), "name": "Checkout contact",
            "allowed_domains": ["shop.example"], "available_fields": list(self.contact),
            "preferred_verification": ["sms", "email"],
        }]))
        for field, value in self.contact.items():
            events.clear()
            result = await self.fill(tools, runtime, field)
            self.assertEqual(result, {
                "ok": True, "kind": "success", "completed": [f"fill_credential.{field}[1]"],
                "failed": None, "notes": [],
            })
            self.assertEqual(browser.fills[-1], (1, value))
            self.assertEqual(events, [
                {"event_type": "browser.action.started", "payload": {"action": "fill_credential", "target": "Email"}},
                {"event_type": "browser.action.completed", "payload": {"action": "fill_credential", "target": "Email"}},
            ])
            self.assertNotIn(value, json.dumps(result))
            self.assertNotIn(value, json.dumps(events))
            self.assertNotIn(value, metadata)
        self.assertEqual(self.provider.resolutions, [(self.metadata.id, "shop.example")] * len(self.contact))

    async def test_login_and_password_fill_without_values_in_results_or_events(self):
        secret = CredentialSecret(login="ada-login", password="saved-password")
        self.provider.secrets[self.metadata.id] = secret
        tools, browser, events, runtime = self.browser_tools()
        for field in ("login", "password"):
            result = await self.fill(tools, runtime, field)
            self.assertTrue(result["ok"])
            self.assertEqual(browser.fills[-1], (2 if field == "password" else 1, getattr(secret, field)))
            for value in (secret.login, secret.password):
                self.assertNotIn(value, json.dumps(result))
                self.assertNotIn(value, json.dumps(events))

    async def test_missing_password_does_not_fill_an_empty_value(self):
        tools, browser, _, runtime = self.browser_tools()
        result = await self.fill(tools, runtime, "password")
        self.assertFalse(result["ok"])
        self.assertIn("no saved password", result["error"])
        self.assertEqual(browser.fills, [])

    async def test_provider_errors_are_preserved_in_results_and_events(self):
        errors = (
            "Credential was not found", "Credential is not selected for this run",
            "Credential 'Checkout contact' is not allowed on attacker.example. "
            "Add attacker.example or *.attacker.example to its allowed domains.",
        )
        for error in errors:
            with self.subTest(error=error):
                self.provider.resolve_error = ValueError(error)
                tools, browser, events, runtime = self.browser_tools(hostname="attacker.example")
                result = await self.fill(tools, runtime, "email")
                self.assertEqual(result, {
                    "ok": False, "kind": "execution_error", "completed": [],
                    "failed": "fill_credential.email[1]", "error": error, "notes": [],
                })
                self.assertEqual(events[-1], {
                    "event_type": "browser.action.failed",
                    "payload": {"action": "fill_credential", "target": "Email", "error": error},
                })
                self.assertEqual(browser.fills, [])
                self.assertEqual(self.provider.resolutions[-1], (self.metadata.id, "attacker.example"))

    async def test_no_provider_lists_nothing_and_reports_storage_error_on_fill(self):
        tools, browser, _, runtime = self.browser_tools(provider=False)
        self.assertEqual(await tools["list_selected_credentials"](runtime), "[]")
        result = await self.fill(tools, runtime, "email")
        self.assertEqual(result["error"], "Credential storage is not configured")
        self.assertEqual(browser.fills, [])

    async def test_empty_provider_lists_nothing_and_refuses_unselected_fill(self):
        self.provider.metadata = ()
        self.provider.secrets = {}
        tools, browser, _, runtime = self.browser_tools()
        self.assertEqual(await tools["list_selected_credentials"](runtime), "[]")
        result = await self.fill(tools, runtime, "email")
        self.assertEqual(result["error"], "Credential is not selected for this run")
        self.assertEqual(browser.fills, [])
