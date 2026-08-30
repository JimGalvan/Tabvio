import json
import logging
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import Frame as PlaywrightFrame

from entities import BrowserState, Element, Frame, Tab
from helpers import Helpers

logger = logging.getLogger(__name__)


class BrowserSession:
    LOAD_TIMEOUT_MS = 60_000
    FRAME_WIDTH = 960
    FRAME_HEIGHT = 540
    FRAME_QUALITY = 55
    OBSERVE_ATTEMPTS = 3

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._frame: PlaywrightFrame | None = None
        self._elements: list[Element] = []
        self._tabs_by_id: dict[str, Page] = {}
        self._frames_by_id: dict[str, PlaywrightFrame] = {}
        self._next_tab_id = 0
        self._next_frame_id = 0
        self._browser_state = BrowserState([], [])
        self._scan_page_javascript: str | None = None

    @property
    def is_open(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    async def _initialize_browser(self) -> Browser:
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
            self._context = await self._browser.new_context(
                viewport={"width": 1365, "height": 768}
            )

        return self._browser

    def _reset_page_state(self, page: Page | None = None) -> None:
        self._page = page
        self._frame = page.main_frame if page else None
        self._frames_by_id = {}
        self._next_frame_id = 0
        self._elements = []

    async def navigate_and_observe(self, url: str) -> str:
        await self._initialize_browser()

        if self._context is None:
            raise RuntimeError("Browser context was not initialized")

        if self._page is None or self._page.is_closed():
            self._reset_page_state(await self._context.new_page())

        await self._page.goto(url, timeout=self.LOAD_TIMEOUT_MS)
        self._reset_page_state(self._page)
        return await self._observe_current_page()

    async def observe_page(self) -> str:
        if not self.is_open:
            raise RuntimeError("No page is open")

        return await self._observe_current_page()

    async def _collect_browser_state(self) -> BrowserState:
        if self._context is None:
            self._browser_state = BrowserState([], [])
            return self._browser_state

        pages = [page for page in self._context.pages if not page.is_closed()]

        if self._page not in pages:
            self._reset_page_state(pages[-1] if pages else None)

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

    def _active_frame(self) -> PlaywrightFrame:
        if not self.is_open or self._page is None:
            raise RuntimeError("No page is open")

        if self._frame not in self._page.frames:
            self._frame = self._page.main_frame

        return self._frame

    async def _scan_page(self) -> str:
        """Scan the page, following it if it navigates mid-scan."""
        if not self._scan_page_javascript:
            scan_page_path = (
                Path(__file__).resolve().parent / "agent_scripts" / "scan-page.js"
            )
            self._scan_page_javascript = scan_page_path.read_text(encoding="utf-8")

        final_attempt = self.OBSERVE_ATTEMPTS - 1
        for attempt in range(self.OBSERVE_ATTEMPTS):
            try:
                frame = self._active_frame()
                await frame.wait_for_load_state("domcontentloaded", timeout=self.LOAD_TIMEOUT_MS)
                await frame.wait_for_load_state("load", timeout=self.LOAD_TIMEOUT_MS)
                return await frame.evaluate(self._scan_page_javascript)
            except Exception as exception:
                if attempt == final_attempt:
                    raise
                logger.info("Page navigated mid-scan, observing the new document: %s",exception)
                self._reset_page_state(self._page)
        raise RuntimeError("The page kept navigating and could not be observed")

    async def _observe_current_page(self) -> str:
        scan_result = await self._scan_page()
        result = json.loads(scan_result)

        self._elements = []
        for index, raw_element in enumerate(result["elements"]):
            self._elements.append(Element(index=index, **raw_element))

        page_content = Helpers.format_page_to_llm_output(result)
        await self._collect_browser_state()
        return (
            f"{page_content}\n"
            f"<available-tabs>{self._browser_state.tabs}\n</available-tabs>"
            f"<available-iframes>{self._browser_state.frames}</available-iframes>"
        )

    def get_stored_element(self, element_index: int) -> Element | None:
        for element in self._elements:
            if element.index == element_index:
                return element

        return None

    async def get_text_in_viewport(self) -> str:
        if not self.is_open:
            raise RuntimeError("No page is open")

        javascript_path = (
            Path(__file__).resolve().parent / "agent_scripts" / "get-text-in-viewport.js"
        )
        javascript = javascript_path.read_text(encoding="utf-8")
        return await self._active_frame().evaluate(javascript)

    async def scroll(self, amount: float) -> str:
        if not self.is_open:
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

    async def _page_coordinates(self, horizontal: float, vertical: float) -> tuple[float, float]:
        if self._page is None:
            raise RuntimeError("No page is open")

        frame = self._active_frame()
        if frame is self._page.main_frame:
            return horizontal, vertical

        frame_element = await frame.frame_element()
        bounding_box = await frame_element.bounding_box()
        if bounding_box is None:
            raise RuntimeError("The selected frame is not visible")

        border = await frame_element.evaluate(
            "element => ({horizontal: element.clientLeft, vertical: element.clientTop})"
        )
        return (
            bounding_box["x"] + border["horizontal"] + horizontal,
            bounding_box["y"] + border["vertical"] + vertical,
        )

    async def _click_element(self, element_index: int) -> None:
        if self._page is None:
            raise RuntimeError("No page is open")

        element = self.get_stored_element(element_index)
        if element is None:
            raise ValueError(f"Element [{element_index}] is not available")

        horizontal, vertical = await self._page_coordinates(element.cx, element.cy)
        await self._page.mouse.click(horizontal, vertical)

    async def click(self, element_index: int) -> str:
        await self._click_element(element_index)
        return f"Clicked element [{element_index}]"

    async def fill(self, element_index: int, value: str) -> str:
        if self._page is None:
            raise RuntimeError("No page is open")

        await self._click_element(element_index)
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.insert_text(value)
        return f"Filled element [{element_index}]"

    async def select(self, element_index: int, value: str) -> str:
        if self._page is None:
            raise RuntimeError("No page is open")

        await self._click_element(element_index)
        await self._page.keyboard.type(value)
        await self._page.keyboard.press("Enter")
        return f"Selected an option in element [{element_index}]"

    async def press(self, element_index: int, value: str) -> str:
        if self._page is None:
            raise RuntimeError("No page is open")

        await self._click_element(element_index)
        await self._page.keyboard.press(value)
        return f"Pressed {value} on element [{element_index}]"

    async def switch_tab(self, tab_id: str) -> str:
        if self._context is None:
            raise RuntimeError("No browser is open")

        await self._collect_browser_state()
        page = self._tabs_by_id.get(tab_id)
        if page is None or page.is_closed():
            return f"Tab with id {tab_id} not found"

        await page.bring_to_front()
        self._reset_page_state(page)
        await self._collect_browser_state()
        return f"Switched to tab with title {await page.title()} and url {page.url}"

    async def capture_frame(self) -> bytes | None:
        if not self.is_open or self._page is None:
            return None

        return await self._page.screenshot(
            type="jpeg",
            quality=self.FRAME_QUALITY,
            scale="css",
        )

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None

        if self._browser is not None:
            await self._browser.close()
            self._browser = None

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

        self._reset_page_state()
        self._tabs_by_id = {}
        self._frames_by_id = {}
        self._next_tab_id = 0
        self._next_frame_id = 0
        self._browser_state = BrowserState([], [])
