import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tabvio_agent.browser.browser_manager import BrowserManager
from tabvio_agent.browser.session import BrowserSession


class FormValueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.main_frame = SimpleNamespace(
            name="", url="https://shop.example", is_detached=Mock(return_value=False),
        )
        self.page = SimpleNamespace(
            main_frame=self.main_frame, frames=[self.main_frame],
            is_closed=Mock(return_value=False),
        )
        self.browser = BrowserSession(BrowserManager())
        self.browser._reset_page_state(self.page)

    async def read(self, fields):
        with patch(
            "tabvio_agent.browser.session.evaluate_with_timeout",
            AsyncMock(return_value=fields),
        ):
            return await self.browser.get_form_values()

    async def test_each_visible_control_is_reported_as_a_label_and_value(self):
        values = await self.read([
            {"label": "Name", "value": "Ada"},
            {"label": "Size", "value": "9"},
        ])

        self.assertEqual(values, ["Name: Ada", "Size: 9"])

    async def test_a_blank_control_says_so_instead_of_reading_as_nothing(self):
        values = await self.read([
            {"label": "First Name", "value": ""},
            {"label": "Last Name", "value": "   "},
        ])

        self.assertEqual(values, ["First Name: (empty)", "Last Name: (empty)"])

    async def test_the_script_masks_a_private_value_before_python_sees_it(self):
        values = await self.read([{"label": "Card number", "value": "filled"}])

        self.assertEqual(values, ["Card number: filled"])
