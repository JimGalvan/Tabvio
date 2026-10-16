import unittest

from tabvio_agent.sensitive_input import SensitiveInputChannel


class SensitiveInputChannelTests(unittest.TestCase):
    """The channel keeps one request, and survives a withdrawn one being replayed."""

    def test_a_withdrawn_request_can_still_be_cleared(self) -> None:
        channel = SensitiveInputChannel()
        request = channel.begin(4, "Enter the code")

        channel.withdraw("the person walked away")

        self.assertIsNone(channel.pending)
        self.assertTrue(channel.is_withdrawn)
        channel.clear(request.id)
        self.assertFalse(channel.is_withdrawn)

    def test_the_reason_is_handed_over_only_once(self) -> None:
        channel = SensitiveInputChannel()
        channel.begin(4, "Enter the code")
        channel.withdraw("the person walked away")

        self.assertEqual(channel.take_withdrawn_reason(), "the person walked away")
        self.assertIsNone(channel.take_withdrawn_reason())
