import logging
from dataclasses import replace
from uuid import UUID

from tabvio_agent import BrowserAgentSession

from tabvio.config import read_agent_settings
from tabvio.credentials.agent_provider import CredentialServiceProvider
from tabvio.credentials.service import CredentialService
from tabvio.runs import constants

logging.getLogger("dotenv.main").setLevel(logging.ERROR)


def build_agent_runtime(
        thread_id: UUID,
        user_id: UUID | None,
        credential_ids: tuple[UUID, ...] = (),
        credential_service: CredentialService | None = None,
        headless: bool | None = None,
        max_runtime_seconds: float = constants.DEFAULT_MAX_RUNTIME_SECONDS,
        sensitive_input_window_seconds: float = constants.DEFAULT_SENSITIVE_INPUT_WINDOW_SECONDS,
) -> BrowserAgentSession:
    settings = replace(
        read_agent_settings(thread_id),
        max_runtime_seconds=max_runtime_seconds,
        sensitive_input_window_seconds=sensitive_input_window_seconds,
    )
    if headless is not None:
        settings = replace(settings, headless=headless)
    credential_provider = None
    if credential_service and credential_ids and user_id:
        credential_provider = CredentialServiceProvider(credential_service, user_id, credential_ids)
    return BrowserAgentSession(
        settings, credential_provider=credential_provider, selected_credential_ids=credential_ids,
    )
