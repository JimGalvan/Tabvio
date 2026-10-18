from dataclasses import dataclass
from pathlib import Path

from tabvio_agent.constants import (
    DEFAULT_FAST_MODEL,
    DEFAULT_MAX_RUNTIME_SECONDS,
    DEFAULT_SENSITIVE_INPUT_WINDOW_SECONDS,
    DEFAULT_STRONG_MODEL,
)


@dataclass(frozen=True)
class AgentSettings:
    strong_model: str = DEFAULT_STRONG_MODEL
    fast_model: str = DEFAULT_FAST_MODEL
    headless: bool = True
    browser_channel: str | None = None
    trace_path: Path | None = None
    max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS
    sensitive_input_window_seconds: float = DEFAULT_SENSITIVE_INPUT_WINDOW_SECONDS
