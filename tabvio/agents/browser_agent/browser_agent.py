from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from tabvio.agents.browser_agent.context import AgentContext
from tabvio.agents.browser_agent.prompts import SYSTEM_PROMPT, TODO_SYSTEM_PROMPT
from tabvio.agents.browser_agent.tools import build_browser_tools
from tabvio.agents.credentials.provider import CredentialProvider
from tabvio.agents.page_navigator.page_navigator import build_page_navigator
from tabvio.agents.settings import AgentSettings
from tabvio.agents.shared.llm import build_strong_model
from tabvio.browser.session import BrowserSession
from tabvio.runs.sensitive_input import SensitiveInputChannel


def build_browser_agent(
        browser_session: BrowserSession,
        sensitive_inputs: SensitiveInputChannel,
        settings: AgentSettings,
        credential_provider: CredentialProvider | None = None,
):
    agent_skills_path = Path(__file__).resolve().parent / "skills"
    agent_files_path = Path(__file__).resolve().parent / "files"

    tools = build_browser_tools(
        browser_session,
        credential_provider=credential_provider,
        sensitive_inputs=sensitive_inputs,
        settings=settings,
    )
    page_navigator_subagent = build_page_navigator(browser_session, settings)

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
        model=build_strong_model(settings.strong_model),
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
            TodoListMiddleware(system_prompt=TODO_SYSTEM_PROMPT),
        ],
        context_schema=AgentContext,
    )
    return agent
