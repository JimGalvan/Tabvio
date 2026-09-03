"""Page-load detector agent.

Kept out of `subagents` so that `tabvio.browser.tools` can import it without
pulling in `build_page_navigator`, which imports the browser tools back.
"""

from typing import Any

from langchain.agents import create_agent

from tabvio.agent.llm import model
from tabvio.agent.prompts import PAGE_LOAD_DETECTOR_PROMPT


def build_page_loader_detector_subagent() -> Any:
    """Build the agent that judges whether a page snapshot looks loaded."""
    return create_agent(model=model, system_prompt=PAGE_LOAD_DETECTOR_PROMPT)
