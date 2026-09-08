"""Score the payment-surface detector against the pages in this directory.

Detection quality is a number, not a feeling, so this runs the real script in a
real browser over a labelled corpus and prints where it was wrong. Grow the
corpus by saving a page here and adding it to EXPECTED below.

    python tests/e2e/checkout/check_detection.py

A page is expected to trigger when a person should be the one filling it in.
The negatives matter as much as the positives: a detector that fires on every
login page is worse than useless.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tabvio.browser.session import BrowserSession  # noqa: E402

PAGES = Path(__file__).resolve().parent

EXPECTED = {
    # Pages a person has to finish themselves.
    "card-form.html": True,
    "sectioned-card-form.html": True,
    "hosted-fields.html": True,
    "braintree-fields.html": True,
    # Pages the agent must stay free to work on.
    "sdk-only.html": False,
    "login.html": False,
    "shipping.html": False,
    "cart.html": False,
    # The realistic flow next door, which is the same judgement on pages
    # that look like a shop rather than like a test case.
    "../shop/product.html": False,
    "../shop/cart.html": False,
    "../shop/shipping.html": False,
    "../shop/payment.html": True,
    "../shop/payment-hosted.html": True,
    "../shop/confirm.html": False,
}


async def main() -> int:
    session = BrowserSession(headless=True)
    failures = 0
    try:
        for name, should_trigger in EXPECTED.items():
            await session.attempt_navigate_and_observe(
                (PAGES / name).resolve().as_uri()
            )
            # Each page is judged on its own, not on what came before it.
            detection = session._payment_detection_result
            triggered = detection.needs_handoff(None)
            signals = ", ".join(
                f"{signal.type}:{signal.value}" for signal in detection.signals
            )

            verdict = "ok " if triggered == should_trigger else "MISS"
            if triggered != should_trigger:
                failures += 1
            print(f"{verdict}  {name:30} triggered={str(triggered):5}  {signals}")
    finally:
        await session.close()

    print(f"\n{len(EXPECTED) - failures}/{len(EXPECTED)} correct")
    return 1 if failures else 0


raise SystemExit(asyncio.run(main()))
