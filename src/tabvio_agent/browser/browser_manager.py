from playwright.async_api import Browser, Playwright, async_playwright

from tabvio_agent.browser.constants import BROWSER_LAUNCH_ARGS


class BrowserManager:
    """Owns the Playwright driver and the browser the sessions open their contexts in."""

    def __init__(self, headless: bool = True, channel: str | None = None):
        self._headless = headless
        self._channel = channel
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                channel=self._channel,
                args=BROWSER_LAUNCH_ARGS,
            )
        return self._browser

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
