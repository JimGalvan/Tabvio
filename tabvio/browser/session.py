import json
import logging
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import Frame as PlaywrightFrame

from tabvio.browser.constants import OBSERVE_ATTEMPTS, LOAD_TIMEOUT_MS, FRAME_QUALITY
from tabvio.browser.formatting import Helpers
from tabvio.browser.models import BrowserState, Element, Frame, Tab

logger = logging.getLogger(__name__)

Registered = TypeVar("Registered")


class BrowserSession:

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._frame: PlaywrightFrame | None = None
        self._elements: list[Element] = []
        self._tabs_by_id: dict[str, Page] = {}
        self._iframes_by_id: dict[str, PlaywrightFrame] = {}
        self._next_tab_id = 0
        self._next_frame_id = 0
        self._scripts: dict[str, str] = {}

    @property
    def is_open(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    @property
    def current_hostname(self) -> str:
        hostname = urlsplit(self._require_page().url).hostname
        if not hostname:
            raise RuntimeError("The current page has no valid hostname")
        return hostname

    def _require_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            raise RuntimeError("No page is open")
        return self._page

    def _script(self, name: str) -> str:
        if name not in self._scripts:
            script_path = Path(__file__).resolve().parent / "scripts" / name
            self._scripts[name] = script_path.read_text(encoding="utf-8")
        return self._scripts[name]

    async def _initialize_browser(self) -> Browser:
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1365, "height": 768}
            )

        return self._browser

    def _reset_page_state(self, page: Page | None = None) -> None:
        self._page = page
        self._frame = page.main_frame if page else None
        self._iframes_by_id = {}
        self._next_frame_id = 0
        self._elements = []

    async def attempt_navigate_and_observe(self, url: str) -> str:
        await self._initialize_browser()

        if self._page is None or self._page.is_closed():
            self._reset_page_state(await self._context.new_page())

        await self._page.goto(url, timeout=LOAD_TIMEOUT_MS)
        self._reset_page_state(self._page)
        return await self._observe_current_page()

    async def attempt_observe_page(self) -> str:
        return await self._observe_current_page()

    @staticmethod
    def _sync_registry(
        registry: dict[str, Registered],
        live_items: list[Registered],
        prefix: str,
        next_id: int,
    ) -> tuple[dict[str, Registered], int]:
        """Drop ids for items that are gone, keep the rest, and number the new ones."""
        synced = {
            item_id: item for item_id, item in registry.items() if item in live_items
        }

        registered = list(synced.values())
        for item in live_items:
            if item not in registered:
                synced[f"{prefix}:{next_id}"] = item
                next_id += 1
                registered.append(item)

        return synced, next_id

    async def _collect_browser_state(self) -> BrowserState:
        if self._context is None:
            return BrowserState([], [])

        pages = [page for page in self._context.pages if not page.is_closed()]

        if self._page not in pages:
            self._reset_page_state(pages[-1] if pages else None)

        self._tabs_by_id, self._next_tab_id = self._sync_registry(
            self._tabs_by_id, pages, "tab", self._next_tab_id
        )

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
            self._iframes_by_id = {}
            return BrowserState(tabs, [])

        page_frames = list(self._page.frames)
        if self._frame not in page_frames:
            self._frame = self._page.main_frame

        self._iframes_by_id, self._next_frame_id = self._sync_registry(
            self._iframes_by_id, page_frames, "frame", self._next_frame_id
        )

        frames = [
            Frame(
                id=frame_id,
                selected=frame is self._frame,
                main=frame is self._page.main_frame,
                name=frame.name or "",
                url=frame.url,
            )
            for frame_id, frame in self._iframes_by_id.items()
        ]

        return BrowserState(tabs, frames)

    def _active_frame(self) -> PlaywrightFrame:
        page = self._require_page()

        if self._frame not in page.frames:
            self._frame = page.main_frame

        return self._frame

    async def _scan_page(self) -> str:
        """Scan the page, following it if it navigates mid-scan."""
        final_attempt = OBSERVE_ATTEMPTS - 1
        for attempt in range(OBSERVE_ATTEMPTS):
            try:
                frame = self._active_frame()
                await frame.wait_for_load_state(
                    "domcontentloaded", timeout=LOAD_TIMEOUT_MS
                )
                await frame.wait_for_load_state("load", timeout=LOAD_TIMEOUT_MS)
                return await frame.evaluate(self._script("scan-page.js"))
            except Exception as exception:
                if attempt == final_attempt:
                    raise
                logger.info(
                    "Page navigated mid-scan, observing the new document: %s", exception
                )
                self._reset_page_state(self._page)
        raise RuntimeError("The page kept navigating and could not be observed")

    async def _observe_current_page(self) -> str:
        result = json.loads(await self._scan_page())

        self._elements = [
            Element(index=index, **raw_element)
            for index, raw_element in enumerate(result["elements"])
        ]

        page_content = Helpers.format_page_to_llm_output(result)
        browser_state = await self._collect_browser_state()
        return (
            f"{page_content}\n"
            f"<available-tabs>{browser_state.tabs}\n</available-tabs>"
            f"<available-iframes>{browser_state.frames}</available-iframes>"
        )

    def get_stored_element(self, element_index: int) -> Element | None:
        if 0 <= element_index < len(self._elements):
            return self._elements[element_index]

        return None

    async def get_text_in_viewport(self) -> str:
        return await self._active_frame().evaluate(
            self._script("get-text-in-viewport.js")
        )

    async def scroll(self, amount: float) -> str:
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

    async def _page_coordinates(
        self, horizontal: float, vertical: float
    ) -> tuple[float, float]:
        page = self._require_page()

        frame = self._active_frame()
        if frame is page.main_frame:
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
        page = self._require_page()

        element = self.get_stored_element(element_index)
        if element is None:
            raise ValueError(f"Element [{element_index}] is not available")

        horizontal, vertical = await self._page_coordinates(element.cx, element.cy)
        await page.mouse.click(horizontal, vertical)

    async def click(self, element_index: int) -> str:
        await self._click_element(element_index)
        return f"Clicked element [{element_index}]"

    async def fill(self, element_index: int, value: str) -> str:
        page = self._require_page()

        await self._click_element(element_index)
        await page.keyboard.press("Control+A")
        await page.keyboard.insert_text(value)
        return f"Filled element [{element_index}]"

    async def fill_sensitive(self, element_index: int, value: str) -> str:
        """Mask a field in browser captures before inserting sensitive text."""
        element = self.get_stored_element(element_index)
        if element is None:
            raise ValueError(f"Element [{element_index}] is not available")
        await self._active_frame().evaluate(
            """
            ({x, y}) => {
                const hit = document.elementFromPoint(x, y);
                const field = hit?.closest('input, textarea');
                if (!field) throw new Error('Sensitive target is not an input');
                field.dataset.tabvioSensitive = 'true';
                field.style.setProperty('-webkit-text-security', 'disc', 'important');
            }
            """,
            {"x": element.cx, "y": element.cy},
        )
        return await self.fill(element_index, value)

    async def select(self, element_index: int, value: str) -> str:
        page = self._require_page()

        await self._click_element(element_index)
        await page.keyboard.type(value)
        await page.keyboard.press("Enter")
        return f"Selected an option in element [{element_index}]"

    async def press(self, element_index: int, value: str) -> str:
        page = self._require_page()

        await self._click_element(element_index)
        await page.keyboard.press(value)
        return f"Pressed {value} on element [{element_index}]"

    async def switch_tab(self, tab_id: str) -> str:
        if self._context is None:
            raise RuntimeError("No browser is open")

        page = self._tabs_by_id.get(tab_id)
        if page is None or page.is_closed():
            return f"Tab with id {tab_id} not found"

        await page.bring_to_front()
        self._reset_page_state(page)
        return f"Switched to tab with title {await page.title()} and url {page.url}"

    async def switch_to_iframe(self, iframe_id: str) -> str:
        pass

    async def capture_screen_frame(self) -> bytes | None:
        if not self.is_open or self._page is None:
            return None

        return await self._page.screenshot(
            type="jpeg",
            quality=FRAME_QUALITY,
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
        self._next_tab_id = 0
