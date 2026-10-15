import os
import runpy
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import call, patch, sentinel
from tabvio_agent.browser_agent import browser_agent
from tabvio_agent.settings import AgentSettings
from tabvio_agent.shared import llm


class ModelSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_default_models_keep_their_reasoning_effort(self) -> None:
        settings = AgentSettings()
        cases = (
            (llm.build_strong_model, settings.strong_model, "none"),
            (llm.build_fast_model, settings.fast_model, "low"),
        )
        for builder, model, effort in cases:
            with self.subTest(model=model):
                with patch.object(llm, "init_chat_model", return_value=sentinel.model) as initialize:
                    self.assertIs(builder(model), sentinel.model)
                    initialize.assert_called_once_with(model, reasoning_effort=effort)

    def test_agent_settings_have_frozen_library_defaults(self) -> None:
        settings = AgentSettings()
        self.assertEqual(settings.strong_model, "openai:gpt-5.6-luna")
        self.assertEqual(settings.fast_model, "openai:gpt-5-nano")
        self.assertTrue(settings.headless)
        self.assertIsNone(settings.browser_channel)
        self.assertIsNone(settings.trace_path)
        with self.assertRaises(FrozenInstanceError):
            settings.headless = False

    def test_explicit_models_ignore_environment_settings(self) -> None:
        with patch.dict(os.environ, {"TABVIO_STRONG_MODEL": "invalid", "TABVIO_FAST_MODEL": "invalid"}):
            self.assertEqual(AgentSettings(), AgentSettings(strong_model="openai:gpt-5.6-luna"))
            for builder in (llm.build_strong_model, llm.build_fast_model):
                with self.subTest(builder=builder.__name__):
                    with patch.object(llm, "init_chat_model") as initialize:
                        builder("anthropic:explicit-model")
                        initialize.assert_called_once_with("anthropic:explicit-model")

    def test_custom_providers_receive_no_openai_reasoning_option(self) -> None:
        cases = (
            ("strong_model", llm.build_strong_model),
            ("fast_model", llm.build_fast_model),
        )
        for setting, builder in cases:
            with self.subTest(setting=setting):
                with patch.object(llm, "init_chat_model", return_value=sentinel.model) as initialize:
                    self.assertIs(builder("anthropic:some-model"), sentinel.model)
                    initialize.assert_called_once_with("anthropic:some-model")

    def test_custom_openai_models_keep_their_reasoning_effort(self) -> None:
        cases = (
            ("strong_model", llm.build_strong_model, "none"),
            ("fast_model", llm.build_fast_model, "low"),
        )
        for setting, builder, effort in cases:
            with self.subTest(setting=setting):
                with patch.object(llm, "init_chat_model") as initialize:
                    builder("  openai:custom-model  ")
                    initialize.assert_called_once_with("openai:custom-model", reasoning_effort=effort)

    def test_bare_model_ids_fail_before_initializing_a_model(self) -> None:
        cases = (
            ("strong_model", llm.build_strong_model),
            ("fast_model", llm.build_fast_model),
        )
        for setting, builder in cases:
            with self.subTest(setting=setting):
                with patch.object(llm, "init_chat_model") as initialize:
                    with self.assertRaisesRegex(RuntimeError, f"{setting} must use provider:model"):
                        builder("gpt-5-nano")
                    initialize.assert_not_called()

    def test_provider_and_model_must_both_be_present(self) -> None:
        for value in ("", "   ", ":some-model", "openai:", "openai: ", " :some-model"):
            with self.subTest(value=value):
                for builder in (llm.build_strong_model, llm.build_fast_model):
                    with patch.object(llm, "init_chat_model") as initialize:
                        with self.assertRaisesRegex(RuntimeError, "must use provider:model"):
                            builder(value)
                        initialize.assert_not_called()

    def test_importing_model_builders_does_not_load_settings_or_create_models(self) -> None:
        with patch("dotenv.load_dotenv") as load_dotenv:
            with patch("langchain.chat_models.init_chat_model") as initialize:
                runpy.run_path(llm.__file__)
                load_dotenv.assert_not_called()
                initialize.assert_not_called()

    def test_browser_agent_and_page_navigator_use_the_supplied_models(self) -> None:
        settings = AgentSettings(strong_model="openai:browser-model", fast_model="anthropic:navigator-model")
        with patch.dict(os.environ, {"TABVIO_STRONG_MODEL": "invalid", "TABVIO_FAST_MODEL": "invalid"}):
            with patch.object(llm, "init_chat_model", side_effect=[sentinel.fast_model, sentinel.strong_model]) as initialize:
                with patch.object(browser_agent, "create_deep_agent", return_value=sentinel.agent) as create:
                    agent = browser_agent.build_browser_agent(sentinel.browser, sentinel.inputs, settings)
        self.assertIs(agent, sentinel.agent)
        self.assertEqual(initialize.call_args_list, [
            call("anthropic:navigator-model"),
            call("openai:browser-model", reasoning_effort="none"),
        ])
        self.assertIs(create.call_args.kwargs["model"], sentinel.strong_model)
        self.assertIs(create.call_args.kwargs["subagents"][0]["model"], sentinel.fast_model)
