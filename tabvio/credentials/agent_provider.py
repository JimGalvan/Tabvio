import asyncio
from uuid import UUID

from tabvio.agents.credentials.models import CredentialMetadata, CredentialSecret
from tabvio.credentials.service import CredentialService


class CredentialServiceProvider:
    def __init__(
            self,
            credential_service: CredentialService,
            user_id: UUID,
            credential_ids: tuple[UUID, ...],
    ):
        self._credential_service = credential_service
        self._user_id = user_id
        self._credential_ids = tuple(credential_ids)

    async def list_selected(self) -> tuple[CredentialMetadata, ...]:
        selected = await asyncio.to_thread(
            self._credential_service.require_selected, self._credential_ids, self._user_id
        )
        metadata = []
        for item in selected:
            preferred = []
            for method in item.preferred_verification:
                preferred.append(method.value)
            metadata.append(CredentialMetadata(
                id=item.id,
                name=item.name,
                allowed_domains=tuple(item.allowed_domains),
                login_hint=item.login_hint,
                is_default=item.is_default,
                available_fields=tuple(item.available_fields),
                preferred_verification=tuple(preferred),
            ))
        return tuple(metadata)

    async def resolve(self, credential_id: UUID, domain: str) -> CredentialSecret:
        if credential_id not in self._credential_ids:
            raise ValueError("Credential is not selected for this run")
        secret = await asyncio.to_thread(
            self._credential_service.resolve_for_domain, credential_id, self._user_id, domain
        )
        return CredentialSecret(
            login=secret.login,
            password=secret.password,
            email=secret.email,
            first_name=secret.first_name,
            last_name=secret.last_name,
            phone=secret.phone,
        )
