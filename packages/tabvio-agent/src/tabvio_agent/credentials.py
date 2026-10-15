from typing import Protocol
from uuid import UUID

from tabvio_agent.models import CredentialMetadata, CredentialSecret


class CredentialProvider(Protocol):
    async def list_selected(self) -> tuple[CredentialMetadata, ...]:
        ...

    async def resolve(self, credential_id: UUID, domain: str) -> CredentialSecret:
        ...


class SelectedCredentialProvider:
    def __init__(self, provider: CredentialProvider, credential_ids: tuple[UUID, ...]):
        self._provider = provider
        self._credential_ids = credential_ids

    async def list_selected(self) -> tuple[CredentialMetadata, ...]:
        selected = []
        for credential in await self._provider.list_selected():
            if credential.id in self._credential_ids:
                selected.append(credential)
        return tuple(selected)

    async def resolve(self, credential_id: UUID, domain: str) -> CredentialSecret:
        if credential_id not in self._credential_ids:
            raise ValueError("Credential is not selected for this run")
        return await self._provider.resolve(credential_id, domain)
