import os
import runpy
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, sentinel
from uuid import uuid4

from tabvio_agent.settings import AgentSettings

from tabvio import config


class ModelSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

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
