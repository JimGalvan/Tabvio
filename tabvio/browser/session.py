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

from tabvio.browser.constants import (
    FRAME_QUALITY,
    LOAD_TIMEOUT_MS,
    OBSERVE_ATTEMPTS,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
)
from tabvio.browser.formatting import Helpers
from tabvio.browser.models import Element, Iframe, Tab, Observation
from tabvio.browser.payment_detection_result import PaymentDetectionResult
from tabvio.browser.payment_detector import PaymentDetector
from collections import Counter

logger = logging.getLogger(__name__)

Registered = TypeVar("Registered")


class BrowserSession:

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._iframe: PlaywrightFrame | None = None
        self._elements: list[Element] = []
        self._tabs_by_id: dict[str, Page] = {}
        self._iframes_by_id: dict[str, PlaywrightFrame] = {}
        self._next_tab_id = 0
        self._next_iframe_id = 0
        self._scripts: dict[str, str] = {}
        self._payment_detector = PaymentDetector()
        self._payment_detection_result = PaymentDetectionResult()

    @property
    def payment_detection_result(self) -> PaymentDetectionResult:
        return self._payment_detection_result

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

    def _get_script(self, name: str) -> str:
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
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
            )

        return self._browser

    def _reset_page_state(self, page: Page | None = None) -> None:
        self._page = page
        self._iframe = page.main_frame if page else None
        self._iframes_by_id = {}
        self._next_iframe_id = 0
        self._elements = []

    async def attempt_navigate_and_observe(self, url: str) -> Observation:
        await self._initialize_browser()

        if self._page is None or self._page.is_closed():
            self._reset_page_state(await self._context.new_page())

        await self._page.goto(url, timeout=LOAD_TIMEOUT_MS)
        self._reset_page_state(self._page)
        return await self._observe_current_page()

    async def attempt_observe_page(self) -> Observation:
        return await self._observe_current_page()

    @staticmethod
    def _sync_registry(
            registry: dict[str, Registered],
            live_items: list[Registered],
            prefix: str,
            next_id: int,
    ) -> tuple[dict[str, Registered], int]:
        """Drop ids for items that are gone, keep the rest, and number the new ones."""
        synced = {}
        for item_id, item in registry.items():
            if item in live_items:
                synced[item_id] = item

        registered = list(synced.values())
        for item in live_items:
            if item not in registered:
                synced[f"{prefix}:{next_id}"] = item
                next_id += 1
                registered.append(item)

        return synced, next_id

    def _live_pages(self) -> list[Page]:
        if self._context is None:
            return []

        pages = []
        for page in self._context.pages:
            if not page.is_closed():
                pages.append(page)

        return pages

    def _reconcile_current_page(self) -> None:
        if self._context is None:
            return

        pages = self._live_pages()
        if self._page not in pages:
            self._reset_page_state(pages[-1] if pages else None)

    async def _collect_tabs(self) -> list[Tab]:
        self._tabs_by_id, self._next_tab_id = self._sync_registry(
            self._tabs_by_id, self._live_pages(), "tab", self._next_tab_id
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

        return tabs

    def _collect_iframes(self) -> list[Iframe]:
        if self._page is None:
            self._iframes_by_id = {}
            return []

        page_frames = list(self._page.frames)
        if self._iframe not in page_frames:
            self._iframe = self._page.main_frame

        self._iframes_by_id, self._next_iframe_id = self._sync_registry(
            self._iframes_by_id, page_frames, "frame", self._next_iframe_id
        )

        frames = []
        for frame_id, frame in self._iframes_by_id.items():
            frames.append(
                Iframe(
                    id=frame_id,
                    selected=frame is self._iframe,
                    main=frame is self._page.main_frame,
                    name=frame.name or "",
                    url=frame.url,
                )
            )

        return frames

    def _active_frame(self) -> PlaywrightFrame:
        page = self._require_page()

        if self._iframe not in page.frames:
            self._iframe = page.main_frame

        return self._iframe

    async def _scan_page(self) -> str:
        """Scan the page, following it if it navigates mid-scan."""
        final_attempt = OBSERVE_ATTEMPTS - 1
        for attempt in range(OBSERVE_ATTEMPTS):
            try:
                active_iframe = self._active_frame()
                await active_iframe.wait_for_load_state(
                    "domcontentloaded", timeout=LOAD_TIMEOUT_MS
                )
                await active_iframe.wait_for_load_state("load", timeout=LOAD_TIMEOUT_MS)
                return await active_iframe.evaluate(self._get_script("scan-page.js"))
            except Exception as exception:
                if attempt == final_attempt:
                    raise
                logger.info(
                    "Page navigated mid-scan, observing the new document: %s", exception
                )
                self._reset_page_state(self._page)
        raise RuntimeError("The page kept navigating and could not be observed")

    async def _capture_page_snapshot(self, page):
        _SNAPSHOT_JS = """
        () => {
          const out = [];
          const els = document.body ? document.body.querySelectorAll('*') : [];
          let budget = 3000;
          for (const el of els) {
            if (budget-- <= 0) break;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            if (r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) continue;
            const st = getComputedStyle(el);
            if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
            const tag = el.tagName;
            if (tag === 'INPUT') {
              out.push(`input|${el.type}|${el.checked ? 'on' : 'off'}|${el.value}`);
            } else if (tag === 'SELECT') {
              out.push(`select|${el.value}`);
            } else if (tag === 'TEXTAREA') {
              out.push(`textarea|${el.value}`);
            } else {
              const own = Array.from(el.childNodes)
                .filter(n => n.nodeType === 3)
                .map(n => n.textContent.trim())
                .filter(Boolean).join(' ');
              if (own) out.push(own);
            }
          }
          return out.join('\\n');
        }
        """

        try:
            text = await page.evaluate(_SNAPSHOT_JS)
        except Exception:
            return Counter()
        return Counter(line.strip() for line in text.splitlines() if line.strip())

    async def _observe_current_page(self) -> Observation:
        result = json.loads(await self._scan_page())

        self._elements = []
        for index, raw_element in enumerate(result["elements"]):
            self._elements.append(Element(index=index, **raw_element))

        interactable_elements = Helpers.get_elements_as_output_for_llm(result)
        self._reconcile_current_page()
        tabs = await self._collect_tabs()
        frames = self._collect_iframes()
        self._payment_detection_result = await self._payment_detector.detect(self._page)
        page_snapshot = await self._capture_page_snapshot(self._page)
        page_state = (
            f"{interactable_elements}\n"
            f"<available-tabs>{tabs}\n</available-tabs>"
            f"<available-iframes>{frames}</available-iframes>"
            f"{self.payment_detection_result.describe()}"
        )

        return Observation(page_state=page_state, page_snapshot=page_snapshot)

    def get_stored_element(self, element_index: int) -> Element | None:
        if 0 <= element_index < len(self._elements):
            return self._elements[element_index]

        return None

    async def get_text_in_viewport(self) -> str:
        return await self._active_frame().evaluate(
            self._get_script("get-text-in-viewport.js")
        )

    async def scroll(self, amount: float) -> str:
        position = await self._active_frame().evaluate(
            self._get_script("scroll-by-pages.js"),
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
            self._get_script("mask-sensitive-field.js"),
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

    def _invalidate_elements(self) -> None:
        """A person moving the page leaves the agent's element indexes stale."""
        self._elements = []

    async def user_click(self, horizontal: float, vertical: float) -> str:
        await self._require_page().mouse.click(horizontal, vertical)
        self._invalidate_elements()
        return f"Clicked at {horizontal:.0f}, {vertical:.0f}"

    async def user_scroll(
            self,
            horizontal: float,
            vertical: float,
            amount: float,
    ) -> str:
        page = self._require_page()
        await page.mouse.move(horizontal, vertical)
        await page.mouse.wheel(0, amount)
        self._invalidate_elements()
        return f"Scrolled by {amount:.0f} pixels"

    async def user_mouse_down(self, horizontal: float, vertical: float) -> str:
        page = self._require_page()
        await page.mouse.move(horizontal, vertical)
        await page.mouse.down()
        self._invalidate_elements()
        return "Mouse button held"

    async def user_mouse_up(self) -> str:
        await self._require_page().mouse.up()
        self._invalidate_elements()
        return "Mouse button released"

    async def user_key(self, key: str) -> str:
        await self._require_page().keyboard.press(key)
        self._invalidate_elements()
        return f"Pressed {key}"

    async def user_text(self, text: str) -> str:
        await self._require_page().keyboard.insert_text(text)
        self._invalidate_elements()
        return "Typed text"

    async def capture_screen_frame(self, quality: int = FRAME_QUALITY) -> bytes | None:
        if not self.is_open or self._page is None:
            return None

        return await self._page.screenshot(
            type="jpeg",
            quality=quality,
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
        self._payment_detection_result = PaymentDetectionResult()
