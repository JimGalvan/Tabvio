"""Verify a deferred iframe cannot discard checkout content or payment signals.

Run from the repository root:
    uv run python tests/e2e/scanner/check_observation.py

Uses local Chrome and synthetic routed pages only. Keeps production timeouts;
the unloaded frame consumes one five-second wait in each of content collection
and payment detection.
"""

import asyncio

from tabvio.browser.session import BrowserSession

CHECKOUT = """<!doctype html>
<title>Checkout regression</title>
<h1>One taco in your cart</h1>
<button>Place Order</button>
<iframe src="https://payments.test/form" title="Payment form"></iframe>
<iframe loading="lazy" style="display:block; margin-top:10000px"
        src="https://maps.test/map" title="Deferred map"></iframe>
"""
PAYMENT = """<!doctype html>
<input autocomplete="cc-number" aria-label="Card number">
<input autocomplete="cc-exp" aria-label="Expiration">
<input autocomplete="cc-csc" aria-label="Security code">
"""


async def main():
    browser = BrowserSession(browser_channel="chrome")
    try:
        async with asyncio.timeout(40):
            await browser._initialize_browser()

            async def fulfill(route):
                body = PAYMENT if route.request.url == "https://payments.test/form" else CHECKOUT
                await route.fulfill(content_type="text/html", body=body)

            await browser._context.route("https://*.test/**", fulfill)
            observation = await browser.attempt_navigate_and_observe("https://checkout.test/order")

            assert any(frame.url == "" for frame in browser._page.frames), "Lazy state not reproduced"
            assert "One taco in your cart" in observation.page_state
            assert "Frame unavailable" in observation.page_state
            assert "<payment-surface>" in observation.page_state
            assert set(browser.payment_detection_result.get_signals) == {
                "card-autocomplete:cc-number",
                "card-autocomplete:cc-exp",
                "card-autocomplete:cc-csc",
                "pay-button:place-order",
            }
            assert browser.payment_detection_result.needs_handoff(None)
            assert browser.get_stored_element(0) is not None
            print("PASS: deferred frame preserves observation and all four payment signals")
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
