import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tabvio_agent.browser.browser_manager import BrowserManager
from tabvio_agent.browser.models import Bounds, Element
from tabvio_agent.browser.session import BrowserSession

NO_BOUNDS = Bounds(0, 0, 0, 0, 0, 0, 0, 0)


class ActionResultTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.main_frame = SimpleNamespace(
            name="", url="https://shop.example", is_detached=Mock(return_value=False),
        )
        self.keyboard = SimpleNamespace(
            press=AsyncMock(), insert_text=AsyncMock(), type=AsyncMock(),
        )
        self.page = SimpleNamespace(
            main_frame=self.main_frame, frames=[self.main_frame],
            is_closed=Mock(return_value=False),
            mouse=SimpleNamespace(click=AsyncMock()),
            keyboard=self.keyboard,
        )
        self.browser = BrowserSession(BrowserManager())
        self.browser._reset_page_state(self.page)
        self.browser._elements = [
            Element(0, "place", "button", "Place order", "id=submit", 10, 20, NO_BOUNDS),
            Element(1, "size", "select", "Size", "id=size", 30, 40, NO_BOUNDS),
        ]

    async def test_a_click_names_the_element_the_observation_showed(self):
        result = await self.browser.click(0)

        self.assertEqual(result, "Clicked <button id=submit> Place order")

    async def test_a_fill_names_the_element_it_typed_into(self):
        result = await self.browser.fill(0, "Jane")

        self.assertEqual(result, "Filled <button id=submit> Place order")

    async def test_a_selection_quotes_the_value_and_names_the_control(self):
        result = await self.browser.select(1, "nine")

        self.assertEqual(result, "Selected 'nine' in <select id=size> Size")

    async def test_a_key_press_names_the_key_and_the_element(self):
        result = await self.browser.press(0, "ctrl+a")

        self.assertEqual(result, "Pressed Control+a on <button id=submit> Place order")

    async def test_an_element_that_is_gone_falls_back_to_its_index(self):
        self.browser._elements = []

        self.assertEqual(self.browser._describe_element(3), "element [3]")
