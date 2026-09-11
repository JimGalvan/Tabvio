from strands import Agent, tool

from tabvio.agents.strands.llm import build_fast_model
from tabvio.browser.session import BrowserSession

# The LangChain build ran this loop inside a JS interpreter. Plain tool calls cost
# more round trips but drop the quickjs dependency.
SYSTEM_PROMPT = """
Locate an off-screen target from a JSON list of up to five keywords.
Call `get_text_in_viewport`, lowercase the text, and compare it with the keywords.
If no keyword is present, call `scroll` with an amount of 0.5 and look again.
Stop when a keyword is found, when scrolling no longer changes the text, or after
12 scrolls. Report whether a keyword was found and how many times you scrolled.
"""


def build_page_navigator(browser: BrowserSession):
    @tool
    async def get_text_in_viewport() -> str:
        """Return text visible in the current browser viewport."""
        return await browser.get_text_in_viewport()

    @tool
    async def scroll(amount: float) -> str:
        """Scroll by a multiple of the viewport height."""
        return await browser.scroll(amount)

    navigator = Agent(
        model=build_fast_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[get_text_in_viewport, scroll],
        callback_handler=None,
    )
    return navigator.as_tool(
        name="page-navigator",
        description="Locate an off-screen target from a JSON keywords list.",
    )
