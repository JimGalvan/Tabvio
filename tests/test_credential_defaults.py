import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from tabvio.credentials.cipher import KEY_LENGTH_BYTES, LocalAesGcmCredentialCipher
from tabvio.credentials.models import CreateCredentialRequest, UpdateCredentialRequest
from tabvio.credentials.repository import CredentialRepository
from tabvio.credentials.service import CredentialService


class CredentialDefaultTests(unittest.TestCase):
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

    def _create(self, name: str, is_default: bool = False):
        return self._service.create(
            self._owner_id,
            CreateCredentialRequest(
                name=name,
                login="owner@example.com",
                password="hunter2",
                allowed_domains=["github.com"],
                is_default=is_default,
            ),
        )

    def test_a_credential_is_not_default_unless_asked(self) -> None:
        self.assertFalse(self._create("Personal GitHub").is_default)

    def test_a_default_credential_is_marked_in_the_listing(self) -> None:
        self._create("Personal GitHub", is_default=True)

        listed = self._service.list(self._owner_id)

        self.assertEqual([item.is_default for item in listed], [True])

    def test_more_than_one_credential_can_be_default(self) -> None:
        self._create("Personal GitHub", is_default=True)
        self._create("Work GitHub", is_default=True)

        listed = self._service.list(self._owner_id)

        self.assertTrue(all(item.is_default for item in listed))

    def test_defaults_are_listed_before_the_rest(self) -> None:
        self._create("Aaa not default")
        self._create("Zzz default", is_default=True)

        listed = self._service.list(self._owner_id)

        self.assertEqual([item.name for item in listed], ["Zzz default", "Aaa not default"])

    def test_the_flag_can_be_turned_on_and_off(self) -> None:
        credential = self._create("Personal GitHub")

        turned_on = self._service.update(
            credential.id, self._owner_id, UpdateCredentialRequest(is_default=True)
        )
        turned_off = self._service.update(
            credential.id, self._owner_id, UpdateCredentialRequest(is_default=False)
        )

        self.assertTrue(turned_on.is_default)
        self.assertFalse(turned_off.is_default)

    def test_an_unrelated_edit_leaves_the_flag_alone(self) -> None:
        credential = self._create("Personal GitHub", is_default=True)

        renamed = self._service.update(
            credential.id, self._owner_id, UpdateCredentialRequest(name="Renamed")
        )

        self.assertTrue(renamed.is_default)

    def test_a_database_without_the_column_is_migrated(self) -> None:
        """Credentials saved before defaults existed keep working."""
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                INSERT INTO credentials VALUES (
                    '11111111-1111-4111-8111-111111111111',
                    '22222222-2222-4222-8222-222222222222',
                    'Existing', '["github.com"]', 'o***@example.com',
                    X'0102', '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00', NULL
                );
                """
            )
            connection.commit()

        repository = CredentialRepository(older_path)
        repository.initialize()

        stored = repository.list_for_user("22222222-2222-4222-8222-222222222222")
        self.assertEqual([item.name for item in stored], ["Existing"])
        self.assertFalse(stored[0].is_default)


if __name__ == "__main__":
    unittest.main()
