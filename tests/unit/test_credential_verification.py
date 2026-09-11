import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from tabvio.credentials.cipher import KEY_LENGTH_BYTES, LocalAesGcmCredentialCipher
from tabvio.credentials.models import (
    CreateCredentialRequest,
    UpdateCredentialRequest,
    VerificationMethod,
)
from tabvio.credentials.repository import CredentialRepository
from tabvio.credentials.service import CredentialService


class CredentialVerificationPreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._database_path = Path(self._temporary_directory.name) / "tabvio.db"
        self._repository = CredentialRepository(self._database_path)
        self._repository.initialize()
        self._service = CredentialService(
            self._repository, LocalAesGcmCredentialCipher(b"k" * KEY_LENGTH_BYTES)
        )
        self._owner_id = uuid4()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _create(self, name: str, preferred_verification=()):
        return self._service.create(
            self._owner_id,
            CreateCredentialRequest(
                name=name,
                login="owner@example.com",
                password="hunter2",
                allowed_domains=["github.com"],
                preferred_verification=list(preferred_verification),
            ),
        )

    def test_a_credential_has_no_preference_unless_asked(self) -> None:
        self.assertEqual(self._create("Personal GitHub").preferred_verification, [])

    def test_the_preferred_order_survives_a_round_trip(self) -> None:
        self._create("Personal GitHub", ["authenticator_app", "sms"])

        listed = self._service.list(self._owner_id)

        self.assertEqual(
            listed[0].preferred_verification,
            [VerificationMethod.AUTHENTICATOR_APP, VerificationMethod.SMS],
        )

    def test_repeats_are_dropped_and_the_order_is_kept(self) -> None:
        created = self._create("Personal GitHub", ["sms", "email", "sms"])

        self.assertEqual(
            created.preferred_verification,
            [VerificationMethod.SMS, VerificationMethod.EMAIL],
        )

    def test_asking_to_be_asked_means_no_preference(self) -> None:
        credential = self._create("Personal GitHub", ["sms"])

        cleared = self._service.update(
            credential.id,
            self._owner_id,
            UpdateCredentialRequest(preferred_verification=["ask"]),
        )

        self.assertEqual(cleared.preferred_verification, [])

    def test_an_unrelated_edit_leaves_the_preference_alone(self) -> None:
        credential = self._create("Personal GitHub", ["email"])

        renamed = self._service.update(
            credential.id, self._owner_id, UpdateCredentialRequest(name="Renamed")
        )

        self.assertEqual(renamed.preferred_verification, [VerificationMethod.EMAIL])

    def test_an_unknown_method_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._create("Personal GitHub", ["carrier_pigeon"])

    def test_a_database_without_the_column_is_migrated(self) -> None:
        """Credentials saved before verification preferences existed keep working."""
        older_path = Path(self._temporary_directory.name) / "older.db"
        with closing(sqlite3.connect(older_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE credentials (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    allowed_domains_json TEXT NOT NULL,
                    login_hint TEXT NOT NULL,
                    encrypted_payload BLOB,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                INSERT INTO credentials VALUES (
                    '11111111-1111-4111-8111-111111111111',
                    '22222222-2222-4222-8222-222222222222',
                    'Existing', '["github.com"]', 'o***@example.com',
                    X'0102', 0, '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00', NULL
                );
                """
            )
            connection.commit()

        repository = CredentialRepository(older_path)
        repository.initialize()

        stored = repository.list_for_user("22222222-2222-4222-8222-222222222222")
        self.assertEqual([item.preferred_verification for item in stored], [[]])


if __name__ == "__main__":
    unittest.main()
