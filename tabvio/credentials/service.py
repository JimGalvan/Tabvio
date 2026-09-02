from __future__ import annotations

import json
from urllib.parse import urlsplit
from uuid import UUID

from tabvio.clock import utc_now
from tabvio.credentials.cipher import CredentialCipher
from tabvio.credentials.exceptions import (
    CredentialConflictError,
    CredentialDomainDeniedError,
    CredentialInvalidError,
    CredentialNotFoundError,
)
from tabvio.credentials.models import (
    CreateCredentialRequest,
    CredentialMetadata,
    CredentialRecord,
    CredentialSecret,
    UpdateCredentialRequest,
)
from tabvio.credentials.repository import CredentialRepository


class CredentialService:
    def __init__(self, repository: CredentialRepository, cipher: CredentialCipher):
        self._repository = repository
        self._cipher = cipher

    def create(self, user_id: UUID, request: CreateCredentialRequest) -> CredentialMetadata:
        credential = CredentialRecord(
            user_id=user_id,
            name=request.name,
            allowed_domains=self._normalize_domains(request.allowed_domains),
            login_hint=self._mask_login(request.login),
            encrypted_payload=b"",
            is_default=request.is_default,
        )
        secret = CredentialSecret(
            login=request.login,
            password=request.password.get_secret_value(),
        )
        credential.encrypted_payload = self._cipher.encrypt(
            secret.model_dump_json().encode(), self._associated_data(credential)
        )
        try:
            self._repository.save(credential)
        except ValueError as exception:
            raise CredentialConflictError(str(exception)) from exception
        return self._metadata(credential)

    def list(self, user_id: UUID) -> list[CredentialMetadata]:
        return [self._metadata(item) for item in self._repository.list_for_user(user_id)]

    def update(
        self, credential_id: UUID, user_id: UUID, request: UpdateCredentialRequest
    ) -> CredentialMetadata:
        credential = self._require_owned(credential_id, user_id)
        secret = self._decrypt(credential)
        if request.name is not None:
            credential.name = request.name
        if request.allowed_domains is not None:
            credential.allowed_domains = self._normalize_domains(request.allowed_domains)
        if request.login is not None:
            secret.login = request.login
            credential.login_hint = self._mask_login(request.login)
        if request.password is not None:
            secret.password = request.password.get_secret_value()
        if request.is_default is not None:
            credential.is_default = request.is_default
        credential.encrypted_payload = self._cipher.encrypt(
            secret.model_dump_json().encode(), self._associated_data(credential)
        )
        credential.updated_at = utc_now()
        try:
            self._repository.save(credential)
        except ValueError as exception:
            raise CredentialConflictError(str(exception)) from exception
        return self._metadata(credential)

    def revoke(self, credential_id: UUID, user_id: UUID) -> None:
        revoked_at = utc_now().isoformat()
        if not self._repository.revoke(credential_id, user_id, revoked_at):
            raise CredentialNotFoundError("Credential was not found")

    def require_selected(
        self, credential_ids: list[UUID] | tuple[UUID, ...], user_id: UUID
    ) -> list[CredentialMetadata]:
        seen: set[UUID] = set()
        credentials = []
        for credential_id in credential_ids:
            if credential_id in seen:
                continue
            seen.add(credential_id)
            credentials.append(self._metadata(self._require_owned(credential_id, user_id)))
        return credentials

    def resolve_for_domain(
        self, credential_id: UUID, user_id: UUID, hostname: str
    ) -> CredentialSecret:
        credential = self._require_owned(credential_id, user_id)
        normalized_host = self.normalize_domain(hostname)
        if normalized_host not in credential.allowed_domains:
            raise CredentialDomainDeniedError(
                f"Credential {credential.name!r} is not allowed on {normalized_host}"
            )
        return self._decrypt(credential)

    def _require_owned(self, credential_id: UUID, user_id: UUID) -> CredentialRecord:
        credential = self._repository.get_owned(credential_id, user_id)
        if credential is None:
            raise CredentialNotFoundError("Credential was not found")
        return credential

    def _decrypt(self, credential: CredentialRecord) -> CredentialSecret:
        plaintext = self._cipher.decrypt(
            credential.encrypted_payload, self._associated_data(credential)
        )
        return CredentialSecret.model_validate(json.loads(plaintext))

    @staticmethod
    def _associated_data(credential: CredentialRecord) -> bytes:
        return f"tabvio:credential:v1:{credential.user_id}:{credential.id}".encode()

    @classmethod
    def _normalize_domains(cls, domains: list[str]) -> list[str]:
        normalized = []
        for domain in domains:
            try:
                value = cls.normalize_domain(domain)
            except ValueError as exception:
                raise CredentialInvalidError(str(exception)) from exception
            if value not in normalized:
                normalized.append(value)
        return normalized

    @staticmethod
    def normalize_domain(domain: str) -> str:
        candidate = domain.strip().lower()
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
        hostname = parsed.hostname
        if not hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError(f"Invalid domain: {domain}")
        return hostname.rstrip(".").encode("idna").decode("ascii")

    @staticmethod
    def _mask_login(login: str) -> str:
        if "@" in login:
            local, domain = login.rsplit("@", 1)
            return f"{local[:1]}***@{domain}"
        return f"{login[:1]}***{login[-1:] if len(login) > 1 else ''}"

    @staticmethod
    def _metadata(credential: CredentialRecord) -> CredentialMetadata:
        return CredentialMetadata.model_validate(
            credential.model_dump(exclude={"user_id", "encrypted_payload", "revoked_at"})
        )
