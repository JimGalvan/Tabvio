import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

from tabvio_agent.models import CredentialMetadata, CredentialSecret
from tabvio_agent.session import BrowserAgentSession
from tabvio_agent.settings import AgentSettings


class SessionConstructionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        browser_patch = patch("tabvio_agent.session.BrowserSession")
        self.browser = browser_patch.start()
        self.addCleanup(browser_patch.stop)
        agent_patch = patch("tabvio_agent.session.build_browser_agent")
        self.agent = agent_patch.start()
        self.addCleanup(agent_patch.stop)

    def test_settings_build_the_browser_and_agent_inside_the_library(self):
        settings = AgentSettings(
            strong_model="anthropic:strong-model", fast_model="openai:fast-model", headless=False,
            browser_channel="chrome", trace_path=Path("trace.zip"),
        )
        session = BrowserAgentSession(settings)
        self.browser.assert_called_once_with(headless=False, trace_path=settings.trace_path, browser_channel="chrome")
        self.agent.assert_called_once_with(
            self.browser.return_value, session._sensitive_inputs, settings, credential_provider=None,
        )
        self.assertIs(session._runtime.browser, self.browser.return_value)
        self.assertIs(session._runtime.agent, self.agent.return_value)
        self.assertEqual(vars(session._runtime.context), {})
        self.assertIsInstance(UUID(session._runtime.config["configurable"]["thread_id"]), UUID)

    async def test_the_async_provider_is_scoped_to_selected_credentials(self):
        selected_id, unselected_id = uuid4(), uuid4()
        selected = CredentialMetadata(selected_id, "Selected", ("example.com",), "hint", False)
        unselected = CredentialMetadata(unselected_id, "Unselected", ("example.com",), "hint", False)
        secret = CredentialSecret(password="secret")
        provider = Mock(list_selected=AsyncMock(return_value=(selected, unselected)), resolve=AsyncMock(return_value=secret))
        BrowserAgentSession(AgentSettings(), credential_provider=provider, selected_credential_ids=(selected_id,))
        scoped = self.agent.call_args.kwargs["credential_provider"]
        self.assertEqual(await scoped.list_selected(), (selected,))
        self.assertIs(await scoped.resolve(selected_id, "example.com"), secret)
        provider.resolve.assert_awaited_once_with(selected_id, "example.com")
        with self.assertRaisesRegex(ValueError, "not selected"):
            await scoped.resolve(unselected_id, "example.com")
        self.assertEqual(provider.resolve.await_count, 1)

    async def test_empty_selection_does_not_expose_provider_credentials(self):
        credential_id = uuid4()
        metadata = CredentialMetadata(credential_id, "Saved", ("example.com",), "hint", False)
        provider = Mock(list_selected=AsyncMock(return_value=(metadata,)), resolve=AsyncMock())
        BrowserAgentSession(AgentSettings(), credential_provider=provider)
        scoped = self.agent.call_args.kwargs["credential_provider"]
        self.assertEqual(await scoped.list_selected(), ())
        with self.assertRaises(ValueError):
            await scoped.resolve(credential_id, "example.com")
        provider.resolve.assert_not_awaited()

    def test_execution_and_sensitive_input_windows_must_be_positive(self):
        for settings in (AgentSettings(max_runtime_seconds=0), AgentSettings(sensitive_input_window_seconds=0)):
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    BrowserAgentSession(settings)
        self.browser.assert_not_called()
