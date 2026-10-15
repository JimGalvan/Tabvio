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

    def test_a_pay_button_alone_does_not_stop_the_agent(self) -> None:
        self.assertFalse(detection(CHECKOUT_URL, PAY_BUTTON).needs_handoff(None))

    def test_a_pay_button_with_a_card_field_stops_the_agent(self) -> None:
        observed = detection(CHECKOUT_URL, PAY_BUTTON + [CARD_FIELDS[0]])
        self.assertTrue(observed.needs_handoff(None))

    def test_a_hosted_card_field_stops_the_agent(self) -> None:
        for provider in ("stripe", "braintree"):
            with self.subTest(provider=provider):
                observed = detection(CHECKOUT_URL, [("hosted-field", provider)])
                self.assertTrue(observed.needs_handoff(None))
                self.assertEqual(observed.get_signals, [f"hosted-field:{provider}"])
                self.assertIn(f"hosted-field:{provider}", observed.describe())

    def test_a_pay_button_with_a_hosted_card_field_stops_the_agent(self) -> None:
        for provider in ("stripe", "braintree"):
            with self.subTest(provider=provider):
                observed = detection(CHECKOUT_URL, PAY_BUTTON + [("hosted-field", provider)])
                self.assertTrue(observed.needs_handoff(None))
                self.assertFalse(observed.needs_handoff(observed.fingerprint))

    def test_a_pay_button_with_only_an_sdk_signal_does_not_stop_the_agent(self) -> None:
        observed = detection(CHECKOUT_URL, PAY_BUTTON + UNKNOWN_SIGNAL)
        self.assertFalse(observed.needs_handoff(None))

    def test_each_card_autocomplete_field_stops_the_agent_on_its_own(self) -> None:
        for value in ("cc-number", "cc-exp", "cc-exp-month", "cc-exp-year", "cc-csc"):
            with self.subTest(value=value):
                observed = detection(CHECKOUT_URL, [("card-autocomplete", value)])
                self.assertTrue(observed.needs_handoff(None))

    def test_a_signal_the_agent_does_not_act_on_is_ignored(self) -> None:
        observed = detection("https://shop.example.com/products/shoes", UNKNOWN_SIGNAL)
        self.assertFalse(observed.needs_handoff(None))
        self.assertEqual(observed.describe(), "")

    def test_the_same_page_only_stops_the_agent_once(self) -> None:
        acknowledged = detection(CHECKOUT_URL, CARD_FIELDS).fingerprint
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
        acknowledged = detection(CHECKOUT_URL, PAY_BUTTON).fingerprint

        observed = detection(CHECKOUT_URL, PAY_BUTTON + CARD_FIELDS)
        self.assertTrue(observed.needs_handoff(acknowledged))

    def test_a_rising_order_total_is_not_a_new_page(self) -> None:
        acknowledged = detection(CHECKOUT_URL, PAY_BUTTON + CARD_FIELDS).fingerprint

        observed = detection(CHECKOUT_URL, PAY_BUTTON + CARD_FIELDS)
        self.assertFalse(observed.needs_handoff(acknowledged))

    def test_leaving_the_payment_page_clears_the_block(self) -> None:
        acknowledged = detection(CHECKOUT_URL, CARD_FIELDS).fingerprint

        observed = detection("https://shop.example.com/thanks", [])
        self.assertFalse(observed.needs_handoff(acknowledged))


if __name__ == "__main__":
    unittest.main()
