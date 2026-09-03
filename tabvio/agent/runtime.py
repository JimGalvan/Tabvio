import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from tabvio.agent.context import AgentContext
from tabvio.agent.llm import strong_model
from tabvio.agent.prompts import SYSTEM_PROMPT
from tabvio.agent.sensitive_input import SensitiveInputChannel
from tabvio.agent.subagents import build_page_navigator
from tabvio.browser.session import BrowserSession
from tabvio.browser.tools import build_browser_tools
from tabvio.credentials.service import CredentialService

logging.getLogger("dotenv.main").setLevel(logging.ERROR)

AGENT_FILES_DIR = Path(__file__).resolve().parent.parent / "agent-files"


@dataclass
class AgentRuntime:
    agent: Any
    config: dict[str, dict[str, str]]
    browser: BrowserSession
    context: AgentContext
    sensitive_inputs: SensitiveInputChannel


def build_agent_runtime(
        thread_id: UUID,
        user_id: UUID | None,
        credential_ids: tuple[UUID, ...] = (),
        credential_service: CredentialService | None = None,
        headless: bool = True,
) -> AgentRuntime:
    browser = BrowserSession(headless=headless)
    agent_context = AgentContext(user_id=user_id, credential_ids=credential_ids)
    sensitive_inputs = SensitiveInputChannel()

    fallback_model = ChatOpenAI(
        model="gpt-5.6-terra",
        use_responses_api=True,
        reasoning={
            "effort": "low",
        },
    )

    backend = FilesystemBackend(root_dir=AGENT_FILES_DIR, virtual_mode=True)
    tools = build_browser_tools(
        browser,
        credential_service=credential_service,
        sensitive_inputs=sensitive_inputs,
    )
    agent = create_deep_agent(
        model=strong_model,
        backend=backend,
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT,
        skills=["/skills"],
        tools=tools,
        subagents=[build_page_navigator(browser)],
        middleware=[
            ModelRetryMiddleware(
                max_retries=3,
                backoff_factor=2.0,
                initial_delay=1.0,
            ),
            ModelFallbackMiddleware(fallback_model),
        ],
        context_schema=AgentContext,
    )
    config = {"configurable": {"thread_id": str(thread_id)}}
    return AgentRuntime(
        agent=agent,
        config=config,
        browser=browser,
        context=agent_context,
        sensitive_inputs=sensitive_inputs,
    )
