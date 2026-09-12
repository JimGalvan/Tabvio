"""Deciding when a payment page has to stop the agent, and when it must not."""

import unittest

from tabvio.browser.models import PaymentSignal
from tabvio.browser.payment_detection_result import PaymentDetectionResult

CARD_FIELDS = [("card-autocomplete", "cc-number"), ("card-autocomplete", "cc-csc")]
PAY_BUTTON = [("pay-button", "place-order")]
UNKNOWN_SIGNAL = [("payment-sdk", "Stripe")]

CHECKOUT_URL = "https://shop.example.com/checkout"


def detection(url: str, signals: list[tuple[str, str]]) -> PaymentDetectionResult:
    return PaymentDetectionResult(
        url=url,
        signals=tuple(PaymentSignal(type=type_, value=value) for type_, value in signals),
    )


class PaymentDetectionResultTests(unittest.TestCase):
    def test_an_ordinary_page_stops_nothing(self) -> None:
        observed = detection("https://shop.example.com/cart", [])
        self.assertFalse(observed.needs_handoff(None))
        self.assertEqual(observed.describe(), "")

    def test_a_card_field_stops_the_agent(self) -> None:
        observed = detection(CHECKOUT_URL, CARD_FIELDS)
        self.assertTrue(observed.needs_handoff(None))
        self.assertIn("cc-number", observed.describe())

    def test_a_pay_button_stops_the_agent(self) -> None:
        """On a hosted card form the fields are cross-origin, so the button
        the merchant owns is the only thing the top document can see."""
        self.assertTrue(detection(CHECKOUT_URL, PAY_BUTTON).needs_handoff(None))

    def test_a_signal_the_agent_does_not_act_on_is_ignored(self) -> None:
        """Only the kinds listed as handoff kinds stop a run, so a signal
        added for diagnostics can never start blocking pages by accident."""
        observed = detection("https://shop.example.com/products/shoes", UNKNOWN_SIGNAL)
        self.assertFalse(observed.needs_handoff(None))
        self.assertEqual(observed.describe(), "")

    def test_the_same_page_only_stops_the_agent_once(self) -> None:
        acknowledged = detection(CHECKOUT_URL, CARD_FIELDS).fingerprint

        # Observing again mid-task must not stop it a second time.
        observed = detection(CHECKOUT_URL, list(reversed(CARD_FIELDS)))
        self.assertFalse(observed.needs_handoff(acknowledged))

    def test_a_query_string_is_not_a_different_page(self) -> None:
        acknowledged = detection(CHECKOUT_URL, CARD_FIELDS).fingerprint

        observed = detection(f"{CHECKOUT_URL}?step=2&session=abc123", CARD_FIELDS)
        self.assertFalse(observed.needs_handoff(acknowledged))

    def test_a_second_payment_page_stops_the_agent_again(self) -> None:
        acknowledged = detection(CHECKOUT_URL, CARD_FIELDS).fingerprint

        observed = detection("https://other.example.com/pay", CARD_FIELDS)
        self.assertTrue(observed.needs_handoff(acknowledged))

    def test_a_new_signal_on_the_same_page_stops_the_agent_again(self) -> None:
        """A page that grows a card field after the user was asked is a new ask."""
        acknowledged = detection(CHECKOUT_URL, PAY_BUTTON).fingerprint

        observed = detection(CHECKOUT_URL, PAY_BUTTON + CARD_FIELDS)
        self.assertTrue(observed.needs_handoff(acknowledged))

    def test_a_rising_order_total_is_not_a_new_page(self) -> None:
        """Pay buttons carry the total, so the signal records the wording that
        matched rather than the label, and a total that ticks up stays quiet."""
        acknowledged = detection(CHECKOUT_URL, PAY_BUTTON).fingerprint

        observed = detection(CHECKOUT_URL, PAY_BUTTON)
        self.assertFalse(observed.needs_handoff(acknowledged))

    def test_leaving_the_payment_page_clears_the_block(self) -> None:
        acknowledged = detection(CHECKOUT_URL, CARD_FIELDS).fingerprint

        observed = detection("https://shop.example.com/thanks", [])
        self.assertFalse(observed.needs_handoff(acknowledged))


if __name__ == "__main__":
    unittest.main()
