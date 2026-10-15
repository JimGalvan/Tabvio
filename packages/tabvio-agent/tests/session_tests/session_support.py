import asyncio
from unittest.mock import AsyncMock, patch

from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt
from tabvio_agent.session import BrowserAgentSession
from tabvio_agent.settings import AgentSettings
from tabvio_agent.shared.events import publish_custom_event


class ScriptedModel(GenericFakeChatModel):
    disable_streaming: bool = True

    def bind_tools(self, tools, **kwargs):
        return self


class FakeBrowser:
    def __init__(self):
        self.close = AsyncMock()
        self.capture_screen_frame = AsyncMock(return_value=b"jpeg-frame")
        self.fill_sensitive = AsyncMock(return_value="Filled element [8] and submitted it")
        self.user_click = AsyncMock(return_value="Clicked")
        self.user_mouse_down = AsyncMock(return_value="Held")
        self.user_mouse_up = AsyncMock(return_value="Released")
        self.user_scroll = AsyncMock(return_value="Scrolled")
        self.user_key = AsyncMock(return_value="Pressed Enter")
        self.user_text = AsyncMock(return_value="Typed text")


@tool
def ask_person(question: str) -> str:
    """Ask the person a question and wait for the answer."""
    publish_custom_event("input.required", {"question": question})
    return str(interrupt({"question": question}))


def build_session(*messages, settings=None, tools=None, browser=None):
    browser = browser or FakeBrowser()
    model = ScriptedModel(messages=iter(messages))
    def build_agent(browser, channel, settings, **kwargs):
        selected_tools = tools(browser, channel) if callable(tools) else tools
        return create_deep_agent(
            model=model, tools=selected_tools or [ask_person], checkpointer=InMemorySaver(),
        )
    with patch("tabvio_agent.session.BrowserSession", return_value=browser):
        with patch("tabvio_agent.session.build_browser_agent", side_effect=build_agent):
            session = BrowserAgentSession(settings or AgentSettings())
    return session, browser


async def next_event(events, event_type):
    async with asyncio.timeout(2):
        async for event in events:
            if event.event_type == event_type:
                return event
    raise AssertionError(f"Session did not emit {event_type}")


async def ready(session, events):
    while True:
        event = await next_event(events, "run.status")
        if event.status == "ready_for_follow_up":
            return
