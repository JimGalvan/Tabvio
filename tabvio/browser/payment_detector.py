import json
import logging
from pathlib import Path

from playwright.async_api import Page

from tabvio.browser.models import PaymentSignal
from tabvio.browser.payment_detection_result import PaymentDetectionResult

logger = logging.getLogger(__name__)

SCRIPT_NAME = "detect-payment-surface.js"


class PaymentDetector:
    def __init__(self,) -> None:
        self._script: str | None = None

    def _detection_script(self) -> str:
        if self._script is None:
            script_path = Path(__file__).resolve().parent / "scripts" / SCRIPT_NAME
            self._script = script_path.read_text(encoding="utf-8")

        return self._script

    async def detect(self, page: Page | None) -> PaymentDetectionResult:
        if page is None or page.is_closed():
            return PaymentDetectionResult()

        script = self._detection_script()
        signals: list[PaymentSignal] = []

        for frame in list(page.frames):
            try:
                result = json.loads(await frame.evaluate(script))
            except Exception as exception:
                logger.debug(
                    "Payment detection skipped frame %s: %s", frame.url, exception
                )
                continue

            for raw_signal in result.get("signals", []):
                signal = PaymentSignal(
                    type=str(raw_signal.get("type", "")),
                    value=str(raw_signal.get("value", "")),
                )
                if signal not in signals:
                    signals.append(signal)

        return PaymentDetectionResult(url=page.url, signals=tuple(signals))
