import warnings
from typing import Any

from langchain_quickjs import CodeInterpreterMiddleware

from agent_tools import build_page_navigator_tools
from browser_session import BrowserSession
from model_config import model

warnings.filterwarnings(
    "ignore",
    message=r"The class `CodeInterpreterMiddleware` is in beta.*",
)

PAGE_NAVIGATOR_PROMPT = """
Locate an off-screen target from a JSON list of up to five keywords. Use `eval` to call `tools.getTextInViewport({})`, compare lowercase text with the keywords, and call `tools.scroll({amount: 0.5})` until a keyword is found, scrolling stops changing position, or 12 scrolls complete. Report whether a keyword was found and the scroll count.
"""


def build_page_navigator(browser: BrowserSession) -> dict[str, Any]:
    """Build the page-navigator subagent, bound to one browser session."""
    return {
        "name": "page-navigator",
        "description": "Locate an off-screen target from a JSON keywords list.",
        "system_prompt": PAGE_NAVIGATOR_PROMPT,
        "tools": build_page_navigator_tools(browser),
        "model": model,
        "middleware": [
            CodeInterpreterMiddleware(ptc=["get_text_in_viewport", "scroll"])
        ],
    }
