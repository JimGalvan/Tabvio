from typing import Protocol
from uuid import UUID

from tabvio.agents.credentials.models import CredentialMetadata, CredentialSecret


class CredentialProvider(Protocol):
    async def list_selected(self) -> tuple[CredentialMetadata, ...]:
        ...

    async def resolve(self, credential_id: UUID, domain: str) -> CredentialSecret:
        ...
