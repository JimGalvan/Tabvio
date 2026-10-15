import os
import runpy
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, sentinel
from uuid import uuid4

from tabvio import config
from tabvio.agents.settings import AgentSettings
from tabvio.agents.shared import llm


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

    def test_blank_settings_use_the_default(self) -> None:
        with patch.dict(os.environ, {"TABVIO_STRONG_MODEL": "   ", "TABVIO_FAST_MODEL": ""}):
            self.assertEqual(config.read_agent_settings(uuid4()), AgentSettings())

    def test_custom_settings_trim_surrounding_whitespace(self) -> None:
        thread_id = uuid4()
        with patch.dict(os.environ, {
            "TABVIO_STRONG_MODEL": "  anthropic:strong-model  ",
            "TABVIO_FAST_MODEL": "  openai:fast-model  ",
            "TABVIO_HEADLESS": " NO ",
            "TABVIO_BROWSER_CHANNEL": " chrome ",
            "TABVIO_BROWSER_TRACE": " YES ",
        }):
            self.assertEqual(config.read_agent_settings(thread_id), AgentSettings(
                strong_model="anthropic:strong-model",
                fast_model="openai:fast-model",
                headless=False,
                browser_channel="chrome",
                trace_path=config.TRACE_DIRECTORY / f"{thread_id}.zip",
            ))

    def test_web_settings_keep_the_library_defaults(self) -> None:
        self.assertEqual(config.read_agent_settings(uuid4()), AgentSettings())

    def test_web_settings_keep_headless_and_trace_boolean_values(self) -> None:
        thread_id = uuid4()
        for value in ("true", "1", "yes"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"TABVIO_HEADLESS": value, "TABVIO_BROWSER_TRACE": value}):
                    settings = config.read_agent_settings(thread_id)
                    self.assertTrue(settings.headless)
                    self.assertEqual(settings.trace_path, config.TRACE_DIRECTORY / f"{thread_id}.zip")
        for value in ("false", "0", "no"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"TABVIO_HEADLESS": value, "TABVIO_BROWSER_TRACE": value}):
                    settings = config.read_agent_settings(thread_id)
                    self.assertFalse(settings.headless)
                    self.assertIsNone(settings.trace_path)

    def test_blank_browser_channel_uses_the_default(self) -> None:
        with patch.dict(os.environ, {"TABVIO_BROWSER_CHANNEL": "   "}):
            self.assertIsNone(config.read_agent_settings(uuid4()).browser_channel)

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

    def test_app_entry_point_loads_the_project_env_file(self) -> None:
        with TemporaryDirectory() as directory:
            project_directory = Path(directory)
            (project_directory / ".env").write_text("TABVIO_FAST_MODEL=anthropic:from-env\n", encoding="utf-8")
            os.environ["TABVIO_FAST_MODEL"] = "openai:existing-model"
            with patch.object(config, "PROJECT_DIRECTORY", project_directory):
                with patch.dict(sys.modules, {"tabvio.server.routes": SimpleNamespace(app=sentinel.app)}):
                    namespace = runpy.run_path(str(Path(config.__file__).with_name("main.py")))
            self.assertIs(namespace["app"], sentinel.app)
            self.assertEqual(os.environ["TABVIO_FAST_MODEL"], "anthropic:from-env")
