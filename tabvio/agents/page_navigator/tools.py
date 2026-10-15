from langchain_core.tools import BaseTool, tool

from tabvio.browser.session import BrowserSession


def build_page_navigator_tools(browser_session: BrowserSession) -> list[BaseTool]:
    """Build the tools for the page-navigator subagent."""

    @tool
    async def get_text_in_viewport() -> str:
        """Return text visible in the current browser viewport."""
        return await browser_session.get_text_in_viewport()

    @tool
    async def scroll(amount: float) -> str:
        """Scroll by a multiple of the viewport height."""
        return await browser_session.scroll(amount)

    return [get_text_in_viewport, scroll]
