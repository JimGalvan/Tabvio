import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4
from tabvio_agent.browser_agent.context import AgentContext
from tabvio_agent.settings import AgentSettings
from tabvio.credentials.agent_provider import CredentialServiceProvider
from tabvio.runs.runtime import build_agent_runtime


class BuildAgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        browser_patch = patch("tabvio.runs.runtime.BrowserSession")
        self.browser = browser_patch.start()
        self.addCleanup(browser_patch.stop)
        agent_patch = patch("tabvio.runs.runtime.build_browser_agent")
        self.build_agent = agent_patch.start()
        self.addCleanup(agent_patch.stop)
        self.settings = AgentSettings()
        settings_patch = patch("tabvio.runs.runtime.read_agent_settings", return_value=self.settings)
        self.read_settings = settings_patch.start()
        self.addCleanup(settings_patch.stop)

    async def test_selected_credentials_are_scoped_to_the_runtime_user(self):
        thread_id, user_id, credential_id = uuid4(), uuid4(), uuid4()
        service = Mock()
        runtime = build_agent_runtime(thread_id, user_id, (credential_id,), service, headless=False)
        provider = self.build_agent.call_args.kwargs["credential_provider"]
        self.assertIsInstance(provider, CredentialServiceProvider)
        self.assertEqual(runtime.context, AgentContext())
        self.assertEqual(vars(runtime.context), {})
        self.assertEqual(runtime.config, {"configurable": {"thread_id": str(thread_id)}})
        self.assertIs(runtime.browser, self.browser.return_value)
        self.assertIs(runtime.agent, self.build_agent.return_value)
        self.browser.assert_called_once_with(headless=False, trace_path=None, browser_channel=None)
        self.build_agent.assert_called_once_with(
            runtime.browser, runtime.sensitive_inputs, replace(self.settings, headless=False),
            credential_provider=provider,
        )
        self.read_settings.assert_called_once_with(thread_id)
        service.require_selected.return_value = []
        self.assertEqual(await provider.list_selected(), ())
        service.require_selected.assert_called_once_with((credential_id,), user_id)

    def test_runtime_passes_web_settings_to_the_browser_and_agent(self):
        settings = AgentSettings(
            strong_model="anthropic:strong-model",
            fast_model="openai:fast-model",
            headless=False,
            browser_channel="chrome",
            trace_path=Path("trace.zip"),
        )
        self.read_settings.return_value = settings
        runtime = build_agent_runtime(uuid4(), None)
        self.browser.assert_called_once_with(
            headless=False, trace_path=settings.trace_path, browser_channel="chrome",
        )
        self.build_agent.assert_called_once_with(
            runtime.browser, runtime.sensitive_inputs, settings, credential_provider=None,
        )

    def test_explicit_headless_overrides_web_settings_in_both_directions(self):
        for headless in (True, False):
            with self.subTest(headless=headless):
                settings = replace(self.settings, headless=not headless)
                self.read_settings.return_value = settings
                build_agent_runtime(uuid4(), None, headless=headless)
                self.assertEqual(self.browser.call_args.kwargs["headless"], headless)
                self.assertEqual(self.build_agent.call_args.args[2], replace(settings, headless=headless))
                self.assertEqual(settings.headless, not headless)

    def test_provider_is_absent_without_service_selection_or_user(self):
        for user_id, credential_ids, service in (
                (uuid4(), (), Mock()),
                (uuid4(), (uuid4(),), None),
                (None, (uuid4(),), Mock()),
        ):
            with self.subTest(user_id=user_id, credential_ids=credential_ids, service=service):
                build_agent_runtime(uuid4(), user_id, credential_ids, service)
                self.assertIsNone(self.build_agent.call_args.kwargs["credential_provider"])
