import unittest

from tabvio.agents.browser_agent.constants import PAGE_TEXT_CHARACTER_LIMIT
from tabvio.agents.browser_agent.tools import build_browser_tools


class TextOnlyBrowser:
    def __init__(self, page_text):
        self.page_text = page_text
        self.reads = 0

    async def get_page_text(self):
        self.reads += 1
        return self.page_text


def read_page_text_tool(browser):
    for candidate in build_browser_tools(browser):
        if candidate.name == "read_page_text":
            return candidate
    raise AssertionError("read_page_text is not offered to the agent")


class ReadPageTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_text_comes_back_whole(self) -> None:
        browser = TextOnlyBrowser("Main frame button was clicked")
        result = await read_page_text_tool(browser).ainvoke({})
        self.assertEqual(result, "Main frame button was clicked")
        self.assertEqual(browser.reads, 1)

    async def test_long_text_is_capped_with_a_marker(self) -> None:
        browser = TextOnlyBrowser("a" * (PAGE_TEXT_CHARACTER_LIMIT + 25))
        result = await read_page_text_tool(browser).ainvoke({})
        self.assertTrue(result.startswith("a" * PAGE_TEXT_CHARACTER_LIMIT))
        self.assertTrue(result.endswith("…[25 chars omitted]"))
