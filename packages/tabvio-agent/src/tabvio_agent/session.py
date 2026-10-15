from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self
from uuid import UUID

from tabvio_agent.credentials import CredentialProvider
from tabvio_agent.events import AgentEvent
from tabvio_agent.models import BrowserControlAction
from tabvio_agent.settings import AgentSettings


class BrowserAgentSession:
    def __init__(
            self,
            settings: AgentSettings,
            *,
            credential_provider: CredentialProvider | None = None,
            selected_credential_ids: tuple[UUID, ...] = (),
    ) -> None:
        raise NotImplementedError

    async def start(self, task: str) -> None:
        raise NotImplementedError

    async def follow_up(self, task: str) -> None:
        raise NotImplementedError

    async def answer(self, text: str) -> None:
        raise NotImplementedError

    async def submit_sensitive_input(self, request_id: UUID, value: str) -> None:
        raise NotImplementedError

    async def decline_sensitive_input(self, request_id: UUID) -> None:
        raise NotImplementedError

    async def take_control(self) -> None:
        raise NotImplementedError

    async def apply_control(self, action: BrowserControlAction) -> str:
        raise NotImplementedError

    async def release_control(self) -> None:
        raise NotImplementedError

    def screen_frames(self) -> AsyncIterator[bytes]:
        raise NotImplementedError

    def events(self) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError

    async def cancel(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def __aenter__(self) -> Self:
        raise NotImplementedError

    async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
    ) -> None:
        raise NotImplementedError
