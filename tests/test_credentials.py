import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from tabvio.credentials.cipher import KEY_LENGTH_BYTES, LocalAesGcmCredentialCipher
from tabvio.credentials.exceptions import (
    CredentialDomainDeniedError,
    CredentialNotFoundError,
)
from tabvio.credentials.models import CreateCredentialRequest, UpdateCredentialRequest
from tabvio.credentials.repository import CredentialRepository
from tabvio.credentials.service import CredentialService


class TestCipher:
    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        return b"sealed:" + associated_data + b":" + plaintext[::-1]

    def decrypt(self, ciphertext: bytes, associated_data: bytes) -> bytes:
        prefix = b"sealed:" + associated_data + b":"
        if not ciphertext.startswith(prefix):
            raise ValueError("associated data did not match")
        return ciphertext[len(prefix):][::-1]


class RealCipherCredentialServiceTests(unittest.TestCase):
    """The fake cipher above keeps the other tests readable; this one proves the
    real AES-GCM payloads survive a round trip through SQLite."""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        repository = CredentialRepository(Path(self._temporary_directory.name) / "tabvio.db")
        repository.initialize()
        self._service = CredentialService(
            repository, LocalAesGcmCredentialCipher(b"k" * KEY_LENGTH_BYTES)
        )
        self._owner_id = uuid4()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_encrypted_payload_round_trips_through_storage(self) -> None:
        metadata = self._service.create(
            self._owner_id,
            CreateCredentialRequest(
                name="Personal GitHub",
                login="owner@example.com",
                password="correct horse battery staple",
                allowed_domains=["github.com"],
            ),
        )

        secret = self._service.resolve_for_domain(metadata.id, self._owner_id, "github.com")

        self.assertEqual("owner@example.com", secret.login)
        self.assertEqual("correct horse battery staple", secret.password)


class CredentialServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._repository = CredentialRepository(
            Path(self._temporary_directory.name) / "tabvio.db"
        )
        self._repository.initialize()
        self._service = CredentialService(self._repository, TestCipher())
        self._owner_id = uuid4()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_create_lists_only_metadata_and_resolves_on_allowed_domain(self) -> None:
        metadata = self._service.create(
            self._owner_id,
            CreateCredentialRequest(
                name="Personal GitHub",
                login="owner@example.com",
                password="correct horse battery staple",
                allowed_domains=["https://GitHub.com/", "github.com"],
            ),
        )

        stored = self._repository.get_owned(metadata.id, self._owner_id)
        listed = self._service.list(self._owner_id)
        resolved = self._service.resolve_for_domain(
            metadata.id, self._owner_id, "github.com"
        )

        self.assertEqual(listed, [metadata])
        self.assertEqual(metadata.login_hint, "o***@example.com")
        self.assertNotIn(b"correct horse battery staple", stored.encrypted_payload)
        self.assertEqual(resolved.login, "owner@example.com")
        self.assertEqual(resolved.password, "correct horse battery staple")
        self.assertEqual(metadata.allowed_domains, ["github.com"])

    def test_cross_user_and_wrong_domain_are_rejected(self) -> None:
        metadata = self._service.create(
            self._owner_id,
            CreateCredentialRequest(
                name="Store",
                login="owner",
                password="secret",
                allowed_domains=["store.example"],
            ),
        )

        with self.assertRaises(CredentialNotFoundError):
            self._service.resolve_for_domain(metadata.id, uuid4(), "store.example")
        with self.assertRaises(CredentialDomainDeniedError):
            self._service.resolve_for_domain(
                metadata.id, self._owner_id, "attacker.example"
            )

    def test_update_keeps_omitted_secret_fields_and_revoke_removes_access(self) -> None:
        metadata = self._service.create(
            self._owner_id,
            CreateCredentialRequest(
                name="Account",
                login="owner",
                password="secret",
                allowed_domains=["example.com"],
            ),
        )

        updated = self._service.update(
            metadata.id,
            self._owner_id,
            UpdateCredentialRequest(
                name="Renamed", allowed_domains=["login.example.com"]
            ),
        )
        resolved = self._service.resolve_for_domain(
            metadata.id, self._owner_id, "login.example.com"
        )
        self.assertEqual(updated.name, "Renamed")
        self.assertEqual(resolved.login, "owner")
        self.assertEqual(resolved.password, "secret")

        self._service.revoke(metadata.id, self._owner_id)
        with self.assertRaises(CredentialNotFoundError):
            self._service.resolve_for_domain(
                metadata.id, self._owner_id, "login.example.com"
            )

        replacement = self._service.create(
            self._owner_id,
            CreateCredentialRequest(
                name="Renamed",
                login="new-owner",
                password="new-secret",
                allowed_domains=["login.example.com"],
            ),
        )
        self.assertNotEqual(replacement.id, metadata.id)


if __name__ == "__main__":
    unittest.main()
