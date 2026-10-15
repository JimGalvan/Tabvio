from dataclasses import dataclass
from pathlib import Path

from tabvio_agent import constants


@dataclass(frozen=True)
class AgentSettings:
    strong_model: str = constants.DEFAULT_STRONG_MODEL
    fast_model: str = constants.DEFAULT_FAST_MODEL
    headless: bool = True
    browser_channel: str | None = None
    trace_path: Path | None = None
    load_timeout_ms: int = constants.DEFAULT_LOAD_TIMEOUT_MS
    evaluate_timeout_seconds: float = constants.DEFAULT_EVALUATE_TIMEOUT_SECONDS
    scan_budget_seconds: float = constants.DEFAULT_SCAN_BUDGET_SECONDS
