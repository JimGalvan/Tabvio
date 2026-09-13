import asyncio
from pathlib import Path

from tabvio.browser.constants import EVALUATE_TIMEOUT_SECONDS


class BrowserUtils:
    @staticmethod
    def get_js_script(name: str):
        script_path = Path(__file__).resolve().parent / "scripts" / name
        return script_path.read_text(encoding="utf-8")

    @staticmethod
    async def evaluate_with_timeout(frame, script, argument=None):
        try:
            async with asyncio.timeout(EVALUATE_TIMEOUT_SECONDS):
                return await frame.evaluate(script, argument)
        except TimeoutError:
            raise TimeoutError("The page stopped responding to scripts after "
                f"{EVALUATE_TIMEOUT_SECONDS} seconds") from None


evaluate_with_timeout = BrowserUtils.evaluate_with_timeout
