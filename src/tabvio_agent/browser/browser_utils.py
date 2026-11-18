import asyncio
from pathlib import Path

from tabvio_agent.browser.constants import EVALUATE_TIMEOUT_SECONDS, KEY_ALIASES


def get_js_script(name: str) -> str:
    script_path = Path(__file__).resolve().parent / "scripts" / name
    return script_path.read_text(encoding="utf-8")


async def evaluate_with_timeout(frame, script, argument=None):
    try:
        async with asyncio.timeout(EVALUATE_TIMEOUT_SECONDS):
            return await frame.evaluate(script, argument)
    except TimeoutError:
        raise TimeoutError(
            f"The page stopped responding to scripts after {EVALUATE_TIMEOUT_SECONDS} seconds"
        ) from None


def normalize_key(value: str) -> str:
    keys = []
    for part in value.strip().split("+"):
        if part:
            keys.append(KEY_ALIASES.get(part.lower(), part))
    if not keys:
        return value

    return "+".join(keys)
