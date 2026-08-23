import asyncio
import json
import time

from playwright.async_api import async_playwright

from entities import Element
from helpers import Helpers


class BrowserManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, headless=False):
        if self._initialized:
            return
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._elements = []
        self._initialized = True

    async def _init_browser(self):
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(headless=self._headless, args=['--start-maximized'])
            self._context = await self._browser.new_context(no_viewport=True)
        return self._browser

    async def navigate_and_observe(self, url):
        await self._init_browser()

        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

        await self._page.goto(url)
        return await self._observe_current_page()

    async def observe_page(self):
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")

        return await self._observe_current_page()

    async def _observe_current_page(self):
        with open("scan-page.js", "r", encoding="utf-8") as f:
            scan_page_js = f.read()

        await self._page.wait_for_load_state("domcontentloaded", timeout=5)
        await self._page.wait_for_load_state("load", timeout=5)
        result = await self._page.evaluate(scan_page_js)
        result_json = json.loads(result)

        self._elements = []
        for index, raw_element in enumerate(result_json["elements"]):
            element = Element(index=index, **raw_element)
            self._elements.append(element)

        return Helpers.format_page_to_llm_output(result_json)

    def _get_element(self, element_index):
        for current_element in self._elements:
            if current_element.index == element_index:
                return current_element
        return None

    async def get_text_in_viewport(self):
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")

        with open("get-text-in-viewport.js", "r", encoding="utf-8") as f:
            get_text_in_viewport_js = f.read()

        return await self._page.evaluate(get_text_in_viewport_js)

    async def scroll(self, amount):
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")

        position = await self._page.evaluate(
            """amount => {
                window.scrollBy(0, innerHeight * amount);
                return {
                    current: scrollY,
                    maximum: Math.max(document.documentElement.scrollHeight - innerHeight, 0)
                };
            }""",
            amount,
        )
        return f"Scrolled to {position['current']} of {position['maximum']} pixels"

    async def click(self, element_index):
        element = self._get_element(element_index)
        await self._page.mouse.click(element.cx, element.cy)
        return f"Clicked element [{element_index}]"

    async def fill(self, element_index, value):
        element = self._get_element(element_index)
        await self._page.mouse.click(element.cx, element.cy)
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.insert_text(value)
        return f"Filled element [{element_index}]"

    async def select(self, element_index, value):
        element = self._get_element(element_index)
        await self._page.mouse.click(element.cx, element.cy)
        await self._page.keyboard.type(value)
        await self._page.keyboard.press("Enter")
        return f"Selected an option in element [{element_index}]"

    async def press(self, element_index, value):
        element = self._get_element(element_index)
        await self._page.mouse.click(element.cx, element.cy)
        await self._page.keyboard.press(value)
        return f"Pressed {value} on element [{element_index}]"

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
            self._elements = []
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
