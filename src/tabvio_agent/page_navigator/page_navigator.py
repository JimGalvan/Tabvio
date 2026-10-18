from typing import Any

from langchain_quickjs import CodeInterpreterMiddleware

from tabvio_agent.browser.session import BrowserSession
from tabvio_agent.page_navigator.prompts import SYSTEM_PROMPT
from tabvio_agent.page_navigator.tools import build_page_navigator_tools
from tabvio_agent.settings import AgentSettings
from tabvio_agent.shared.llm import build_fast_model


def build_page_navigator(browser: BrowserSession, settings: AgentSettings) -> dict[str, Any]:
    return {
        "name": "page-navigator",
        "description": (
            "Locate an off-screen target by reading viewport text for meaning and context. "
            "Provide a natural-language description of the target and its identifying details. "
            "Returns whether it was found, supporting text, and the reason for stopping."
        ),
        "system_prompt": SYSTEM_PROMPT,
        "tools": build_page_navigator_tools(browser),
        "model": build_fast_model(settings.fast_model),
        "middleware": [
            CodeInterpreterMiddleware(ptc=["get_text_in_viewport", "scroll"])
        ],
    }
