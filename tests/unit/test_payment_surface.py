"""Deciding when a payment page has to stop the agent, and when it must not."""

import unittest

from tabvio.browser.session import BrowserSession

CARD_FIELDS = [
    {"kind": "card-autocomplete", "detail": "cc-number"},
    {"kind": "card-autocomplete", "detail": "cc-csc"},
]
HOSTED_FIELDS = [{"kind": "hosted-payment-field", "detail": "Stripe"}]
SDK_ONLY = [{"kind": "payment-sdk", "detail": "Stripe"}]

CHECKOUT_URL = "https://shop.example.com/checkout"


class PaymentSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = BrowserSession()

    def observe(self, url: str, signals: list[dict[str, str]]) -> None:
        self.session._record_payment_signals(url, signals)

    def test_an_ordinary_page_stops_nothing(self) -> None:
        self.observe("https://shop.example.com/cart", [])
        self.assertFalse(self.session.needs_payment_handoff())
        self.assertEqual(self.session.describe_payment_surface(), "")

    def test_a_card_field_stops_the_agent(self) -> None:
        self.observe(CHECKOUT_URL, CARD_FIELDS)
        self.assertTrue(self.session.needs_payment_handoff())
        self.assertIn("cc-number", self.session.describe_payment_surface())

    def test_hosted_fields_stop_the_agent(self) -> None:
        """The card inputs are cross-origin, so the frame is the whole signal."""
        self.observe(CHECKOUT_URL, HOSTED_FIELDS)
        self.assertTrue(self.session.needs_payment_handoff())

    def test_a_payment_sdk_alone_is_not_enough(self) -> None:
        """Stripe asks sites to load js.stripe.com everywhere for fraud scoring.

        Treating that as a payment page would stop the agent on every page of
        every shop that follows the advice.
        """
        self.observe("https://shop.example.com/products/shoes", SDK_ONLY)
        self.assertFalse(self.session.needs_payment_handoff())
        self.assertEqual(self.session.describe_payment_surface(), "")

    def test_the_same_page_only_stops_the_agent_once(self) -> None:
        self.observe(CHECKOUT_URL, CARD_FIELDS)
        self.session.acknowledge_payment_surface()
        self.assertFalse(self.session.needs_payment_handoff())

        # Observing again mid-task must not stop it a second time.
        self.observe(CHECKOUT_URL, list(reversed(CARD_FIELDS)))
        self.assertFalse(self.session.needs_payment_handoff())

    def test_a_query_string_is_not_a_different_page(self) -> None:
        self.observe(CHECKOUT_URL, CARD_FIELDS)
        self.session.acknowledge_payment_surface()

        self.observe(f"{CHECKOUT_URL}?step=2&session=abc123", CARD_FIELDS)
        self.assertFalse(self.session.needs_payment_handoff())

    def test_a_second_payment_page_stops_the_agent_again(self) -> None:
        self.observe(CHECKOUT_URL, CARD_FIELDS)
        self.session.acknowledge_payment_surface()

        self.observe("https://other.example.com/pay", CARD_FIELDS)
        self.assertTrue(self.session.needs_payment_handoff())

    def test_a_new_signal_on_the_same_page_stops_the_agent_again(self) -> None:
        """A page that grows a card field after the user was asked is a new ask."""
        self.observe(CHECKOUT_URL, HOSTED_FIELDS)
        self.session.acknowledge_payment_surface()

        self.observe(CHECKOUT_URL, HOSTED_FIELDS + CARD_FIELDS)
        self.assertTrue(self.session.needs_payment_handoff())

    def test_leaving_the_payment_page_clears_the_block(self) -> None:
        self.observe(CHECKOUT_URL, CARD_FIELDS)
        self.assertTrue(self.session.needs_payment_handoff())

        self.observe("https://shop.example.com/thanks", [])
        self.assertFalse(self.session.needs_payment_handoff())


if __name__ == "__main__":
    unittest.main()
