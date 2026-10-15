import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from tabvio_agent.settings import AgentSettings

from tabvio.credentials.agent_provider import CredentialServiceProvider
from tabvio.runs.runtime import build_agent_runtime


class BuildAgentSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        session_patch = patch("tabvio.runs.runtime.BrowserAgentSession")
        self.session = session_patch.start()
        self.addCleanup(session_patch.stop)
        self.settings = AgentSettings()
        settings_patch = patch("tabvio.runs.runtime.read_agent_settings", return_value=self.settings)
        self.read_settings = settings_patch.start()
        self.addCleanup(settings_patch.stop)

    async def test_selected_credentials_are_scoped_to_the_session_user(self):
        thread_id, user_id, credential_id = uuid4(), uuid4(), uuid4()
        service = Mock()
        session = build_agent_runtime(thread_id, user_id, (credential_id,), service, headless=False)
        provider = self.session.call_args.kwargs["credential_provider"]
        self.assertIsInstance(provider, CredentialServiceProvider)
        self.assertIs(session, self.session.return_value)
        self.session.assert_called_once_with(
            replace(self.settings, headless=False), credential_provider=provider,
            selected_credential_ids=(credential_id,),
        )
        self.read_settings.assert_called_once_with(thread_id)
        service.require_selected.return_value = []
        self.assertEqual(await provider.list_selected(), ())
        service.require_selected.assert_called_once_with((credential_id,), user_id)

    def test_session_receives_web_settings(self):
        settings = AgentSettings(
            strong_model="anthropic:strong-model", fast_model="openai:fast-model",
            headless=False, browser_channel="chrome", trace_path=Path("trace.zip"),
        )
        self.read_settings.return_value = settings
        build_agent_runtime(uuid4(), None)
        self.session.assert_called_once_with(settings, credential_provider=None, selected_credential_ids=())

    def test_explicit_headless_overrides_web_settings_in_both_directions(self):
        for headless in (True, False):
            with self.subTest(headless=headless):
                settings = replace(self.settings, headless=not headless)
                self.read_settings.return_value = settings
                build_agent_runtime(uuid4(), None, headless=headless)
                self.assertEqual(self.session.call_args.args[0], replace(settings, headless=headless))
                self.assertEqual(settings.headless, not headless)

    def test_provider_is_absent_without_service_selection_or_user(self):
        for user_id, credential_ids, service in (
                (uuid4(), (), Mock()), (uuid4(), (uuid4(),), None), (None, (uuid4(),), Mock()),
        ):
            with self.subTest(user_id=user_id, credential_ids=credential_ids, service=service):
                build_agent_runtime(uuid4(), user_id, credential_ids, service)
                self.assertIsNone(self.session.call_args.kwargs["credential_provider"])

    def test_session_receives_per_run_execution_and_sensitive_input_limits(self):
        build_agent_runtime(uuid4(), None, max_runtime_seconds=420, sensitive_input_window_seconds=120)
        settings = self.session.call_args.args[0]
        self.assertEqual(settings.max_runtime_seconds, 420)
        self.assertEqual(settings.sensitive_input_window_seconds, 120)
