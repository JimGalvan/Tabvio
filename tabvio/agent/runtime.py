import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from tabvio.agent.llm import strong_model
from tabvio.agent.prompts import SYSTEM_PROMPT
from tabvio.agent.subagents import build_page_navigator
from tabvio.browser.session import BrowserSession
from tabvio.browser.tools import build_browser_tools

logging.getLogger("dotenv.main").setLevel(logging.ERROR)


@dataclass
class AgentRuntime:
    agent: Any
    config: dict[str, dict[str, str]]
    browser: BrowserSession


def build_agent_runtime(thread_id: UUID, headless: bool = True) -> AgentRuntime:
    browser = BrowserSession(headless=headless)

    fallback_model = ChatOpenAI(
        model="gpt-5.6-terra",
        use_responses_api=True,
        reasoning={
            "effort": "low",
        },
    )

    agent = create_deep_agent(
        model=strong_model,
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT,
        tools=build_browser_tools(browser),
        subagents=[build_page_navigator(browser)],
        middleware=[
            ModelRetryMiddleware(
                max_retries=3,
                backoff_factor=2.0,
                initial_delay=1.0,
            ),
            ModelFallbackMiddleware(fallback_model),
        ],
    )
    config = {"configurable": {"thread_id": str(thread_id)}}
    return AgentRuntime(agent=agent, config=config, browser=browser)
