from strands import Agent

from tabvio.agents.strands.page_navigator.prompts import SYSTEM_PROMPT
from tabvio.agents.strands.page_navigator.tools import build_page_navigator_tools
from tabvio.agents.strands.shared.llm import build_fast_model
from tabvio.browser.session import BrowserSession


def build_page_navigator(browser: BrowserSession):
    navigator = Agent(
        model=build_fast_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=build_page_navigator_tools(browser),
        callback_handler=None,
    )
    return navigator.as_tool(
        name="page-navigator",
        description="Locate an off-screen target from a JSON keywords list.",
    )
