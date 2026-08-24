import json

from playwright.async_api import async_playwright

from entities import BrowserState, Element, Frame, Tab
from helpers import Helpers


class BrowserManager:
    _instance = None
    LOAD_TIMEOUT_MS = 60_000

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
        self._frame = None
        self._elements = []
        self._tabs_by_id = {}
        self._frames_by_id = {}
        self._next_tab_id = 0
        self._next_frame_id = 0
        self._initialized = True
        self._browser_state = BrowserState([], [])

    async def _init_browser(self):
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(headless=self._headless, args=['--start-maximized'])
            self._context = await self._browser.new_context(no_viewport=True)
        return self._browser

    def reset_browser_manager_state(self, page=None):
        self._page = page
        self._frame = page.main_frame if page else None
        self._frames_by_id = {}
        self._next_frame_id = 0
        self._elements = []

    async def navigate_and_observe(self, url):
        await self._init_browser()

        if self._page is None or self._page.is_closed():
            self.reset_browser_manager_state(await self._context.new_page())

        await self._page.goto(url)
        self.reset_browser_manager_state(self._page)
        return await self._observe_current_page()

    async def observe_page(self):
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")

        return await self._observe_current_page()

    async def _collect_browser_state(self) -> BrowserState:
        pages = [page for page in self._context.pages if not page.is_closed()]

        if self._page not in pages:
            self.reset_browser_manager_state(pages[-1] if pages else None)

        self._tabs_by_id = {
            tab_id: page
            for tab_id, page in self._tabs_by_id.items()
            if page in pages
        }

        registered_pages = list(self._tabs_by_id.values())
        for page in pages:
            if page not in registered_pages:
                tab_id = f"tab:{self._next_tab_id}"
                self._next_tab_id += 1
                self._tabs_by_id[tab_id] = page
                registered_pages.append(page)

        tabs = []
        for tab_id, page in self._tabs_by_id.items():
            try:
                title = await page.title()
            except Exception:
                title = ""

            tabs.append(
                Tab(
                    id=tab_id,
                    selected=page is self._page,
                    title=title,
                    url=page.url,
                )
            )

        if self._page is None:
            self._frames_by_id = {}
            self._browser_state = BrowserState(tabs, [])
            return self._browser_state

        page_frames = list(self._page.frames)
        if self._frame not in page_frames:
            self._frame = self._page.main_frame

        self._frames_by_id = {
            frame_id: frame
            for frame_id, frame in self._frames_by_id.items()
            if frame in page_frames
        }

        registered_frames = list(self._frames_by_id.values())
        for frame in page_frames:
            if frame not in registered_frames:
                frame_id = f"frame:{self._next_frame_id}"
                self._next_frame_id += 1
                self._frames_by_id[frame_id] = frame
                registered_frames.append(frame)

        frames = [
            Frame(
                id=frame_id,
                selected=frame is self._frame,
                main=frame is self._page.main_frame,
                name=frame.name or "",
                url=frame.url,
            )
            for frame_id, frame in self._frames_by_id.items()
        ]

        self._browser_state = BrowserState(tabs, frames)
        return self._browser_state

    def _active_frame(self):
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")

        if self._frame not in self._page.frames:
            self._frame = self._page.main_frame

        return self._frame

    async def _observe_current_page(self):
        with open("scan-page.js", encoding="utf-8") as f:
            scan_page_js = f.read()

        frame = self._active_frame()
        await frame.wait_for_load_state(
            "domcontentloaded", timeout=self.LOAD_TIMEOUT_MS
        )
        await frame.wait_for_load_state("load", timeout=self.LOAD_TIMEOUT_MS)
        result = await frame.evaluate(scan_page_js)
        result_json = json.loads(result)

        self._elements = []
        for index, raw_element in enumerate(result_json["elements"]):
            element = Element(index=index, **raw_element)
            self._elements.append(element)

        page_content = Helpers.format_page_to_llm_output(result_json)
        await self._collect_browser_state()
        llm_output = (f"{page_content}\n"
                      f"Current available tabs:{self._browser_state.tabs}\n"
                      f"Current available frames:{self._browser_state.frames}"
                      )
        return llm_output

    def get_stored_element(self, element_index):
        for current_element in self._elements:
            if current_element.index == element_index:
                return current_element
        return None

    async def get_text_in_viewport(self):
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")

        with open("get-text-in-viewport.js", encoding="utf-8") as f:
            get_text_in_viewport_js = f.read()

        return await self._active_frame().evaluate(get_text_in_viewport_js)

    async def scroll(self, amount):
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")

        position = await self._active_frame().evaluate(
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

    async def _page_coordinates(self, x, y):
        frame = self._active_frame()
        if frame is self._page.main_frame:
            return x, y

        frame_element = await frame.frame_element()
        bounding_box = await frame_element.bounding_box()
        if bounding_box is None:
            raise RuntimeError("The selected frame is not visible")

        border = await frame_element.evaluate(
            "element => ({x: element.clientLeft, y: element.clientTop})"
        )
        return (
            bounding_box["x"] + border["x"] + x,
            bounding_box["y"] + border["y"] + y,
        )

    async def _click_element(self, element_index):
        element = self.get_stored_element(element_index)
        if element is None:
            raise ValueError(f"Element [{element_index}] is not available")

        x, y = await self._page_coordinates(element.cx, element.cy)
        await self._page.mouse.click(x, y)

    async def click(self, element_index):
        await self._click_element(element_index)
        return f"Clicked element [{element_index}]"

    async def fill(self, element_index, value):
        await self._click_element(element_index)
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.insert_text(value)
        return f"Filled element [{element_index}]"

    async def select(self, element_index, value):
        await self._click_element(element_index)
        await self._page.keyboard.type(value)
        await self._page.keyboard.press("Enter")
        return f"Selected an option in element [{element_index}]"

    async def press(self, element_index, value):
        await self._click_element(element_index)
        await self._page.keyboard.press(value)
        return f"Pressed {value} on element [{element_index}]"

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._context = None
            self.reset_browser_manager_state()
            self._tabs_by_id = {}
            self._next_tab_id = 0
            self._browser_state = BrowserState([], [])
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def switch_tab(self, tab_id: str):
        if self._context is None:
            raise RuntimeError("No browser is open")

        await self._collect_browser_state()
        page = self._tabs_by_id.get(tab_id)
        if page is None or page.is_closed():
            return f"Tab with id {tab_id} not found"

        await page.bring_to_front()
        self.reset_browser_manager_state(page)
        await self._collect_browser_state()
        return f"Switched to tab with title {await page.title()} and url {page.url}"

    async def switch_frame(self, frame_id: str):
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")

        await self._collect_browser_state()
        frame = self._frames_by_id.get(frame_id)
        if frame is None or frame not in self._page.frames:
            return f"Frame with id {frame_id} not found"

        self._frame = frame
        self._elements = []
        await self._collect_browser_state()
        return f"Switched to frame with name {frame.name or ''} and url {frame.url}"
