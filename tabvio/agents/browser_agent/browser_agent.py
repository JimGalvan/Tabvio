from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend
from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from tabvio.agent.context import AgentContext
from tabvio.agent.llm import strong_model
from tabvio.agent.sensitive_input import SensitiveInputChannel
from tabvio.agent.subagents import build_page_navigator
from tabvio.agents.browser_agent.prompts import SYSTEM_PROMPT
from tabvio.browser.session import BrowserSession
from tabvio.browser.tools import build_browser_tools
from tabvio.credentials.service import CredentialService


def build_browser_agent(
        browser_session: BrowserSession,
        sensitive_inputs: SensitiveInputChannel,
        credential_service: CredentialService | None = None,
):
    agent_skills_path = Path(__file__).resolve().parent / "skills"
    agent_files_path = Path(__file__).resolve().parent / "files"

    tools = build_browser_tools(
        browser_session,
        credential_service=credential_service,
        sensitive_inputs=sensitive_inputs,
    )
    page_navigator_subagent = build_page_navigator(browser_session)

    workspace_backend = FilesystemBackend(
        root_dir=agent_files_path,
        virtual_mode=True,
    )
    skills_backend = FilesystemBackend(
        root_dir=agent_skills_path,
        virtual_mode=True,
    )
    backend = CompositeBackend(
        default=workspace_backend,
        routes={
            "/skills/": skills_backend,
        },
    )

    agent = create_deep_agent(
        model=strong_model,
        backend=backend,
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT,
        skills=["/skills"],
        tools=tools,
        subagents=[page_navigator_subagent],
        middleware=[
            ModelRetryMiddleware(
                max_retries=3,
                backoff_factor=2.0,
                initial_delay=1.0,
            ),
            ModelFallbackMiddleware(strong_model),
        ],
        context_schema=AgentContext,
    )
    return agent
