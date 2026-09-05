from typing import Any

from langchain_quickjs import CodeInterpreterMiddleware

from tabvio.agent.llm import model
from tabvio.agents.page_navigator.prompts import SYSTEM_PROMPT
from tabvio.browser.session import BrowserSession
from tabvio.browser.tools import build_page_navigator_tools


def build_page_navigator(browser: BrowserSession) -> dict[str, Any]:
    """Build the page-navigator subagent, bound to one browser session."""
    return {
        "name": "page-navigator",
        "description": "Locate an off-screen target from a JSON keywords list.",
        "system_prompt": SYSTEM_PROMPT,
        "tools": build_page_navigator_tools(browser),
        "model": model,
        "middleware": [
            CodeInterpreterMiddleware(ptc=["get_text_in_viewport", "scroll"])
        ],
    }
