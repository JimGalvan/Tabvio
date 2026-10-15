import os
import runpy
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, sentinel

from tabvio import config
from tabvio.agents.shared import llm


class ModelSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_default_models_keep_their_reasoning_effort(self) -> None:
        cases = (
            (llm.build_strong_model, "openai:gpt-5.6-luna", "none"),
            (llm.build_fast_model, "openai:gpt-5-nano", "low"),
        )
        for builder, model, effort in cases:
            with self.subTest(model=model):
                with patch.object(llm, "init_chat_model", return_value=sentinel.model) as initialize:
                    self.assertIs(builder(), sentinel.model)
                    initialize.assert_called_once_with(model, reasoning_effort=effort)

    def test_custom_providers_receive_no_openai_reasoning_option(self) -> None:
        cases = (
            ("TABVIO_STRONG_MODEL", llm.build_strong_model),
            ("TABVIO_FAST_MODEL", llm.build_fast_model),
        )
        for setting, builder in cases:
            with self.subTest(setting=setting):
                with patch.dict(os.environ, {setting: "anthropic:some-model"}):
                    with patch.object(llm, "init_chat_model", return_value=sentinel.model) as initialize:
                        self.assertIs(builder(), sentinel.model)
                        initialize.assert_called_once_with("anthropic:some-model")

    def test_custom_openai_models_keep_their_reasoning_effort(self) -> None:
        cases = (
            ("TABVIO_STRONG_MODEL", llm.build_strong_model, "none"),
            ("TABVIO_FAST_MODEL", llm.build_fast_model, "low"),
        )
        for setting, builder, effort in cases:
            with self.subTest(setting=setting):
                with patch.dict(os.environ, {setting: "openai:custom-model"}):
                    with patch.object(llm, "init_chat_model") as initialize:
                        builder()
                        initialize.assert_called_once_with("openai:custom-model", reasoning_effort=effort)

    def test_blank_settings_use_the_default(self) -> None:
        for setting in ("TABVIO_STRONG_MODEL", "TABVIO_FAST_MODEL"):
            with self.subTest(setting=setting):
                with patch.dict(os.environ, {setting: "   "}):
                    self.assertEqual(
                        config.read_langchain_model_setting(setting, "openai:default-model"),
                        "openai:default-model",
                    )

    def test_custom_settings_trim_surrounding_whitespace(self) -> None:
        for setting in ("TABVIO_STRONG_MODEL", "TABVIO_FAST_MODEL"):
            with self.subTest(setting=setting):
                with patch.dict(os.environ, {setting: "  anthropic:some-model  "}):
                    self.assertEqual(
                        config.read_langchain_model_setting(setting, "openai:default-model"),
                        "anthropic:some-model",
                    )

    def test_bare_model_ids_fail_before_initializing_a_model(self) -> None:
        cases = (
            ("TABVIO_STRONG_MODEL", llm.build_strong_model),
            ("TABVIO_FAST_MODEL", llm.build_fast_model),
        )
        for setting, builder in cases:
            with self.subTest(setting=setting):
                with patch.dict(os.environ, {setting: "gpt-5-nano"}):
                    with patch.object(llm, "init_chat_model") as initialize:
                        with self.assertRaisesRegex(RuntimeError, f"{setting} must use provider:model"):
                            builder()
                        initialize.assert_not_called()

    def test_provider_and_model_must_both_be_present(self) -> None:
        for value in (":some-model", "openai:", "openai: ", " :some-model"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"TABVIO_STRONG_MODEL": value}):
                    with self.assertRaisesRegex(RuntimeError, "TABVIO_STRONG_MODEL must use provider:model"):
                        config.read_langchain_model_setting("TABVIO_STRONG_MODEL", "openai:default-model")

    def test_legacy_provider_setting_does_not_control_langchain_models(self) -> None:
        with patch.dict(os.environ, {"TABVIO_MODEL_PROVIDER": "bedrock"}):
            with patch.object(llm, "init_chat_model") as initialize:
                llm.build_strong_model()
                initialize.assert_called_once_with("openai:gpt-5.6-luna", reasoning_effort="none")

    def test_legacy_readers_still_accept_bare_model_ids(self) -> None:
        with patch.dict(os.environ, {"TABVIO_STRONG_MODEL": "strong-id", "TABVIO_FAST_MODEL": "fast-id"}):
            self.assertEqual(config.read_strong_model_setting("default"), "strong-id")
            self.assertEqual(config.read_fast_model_setting("default"), "fast-id")

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
