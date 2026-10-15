import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from tabvio.credentials.cipher import KEY_LENGTH_BYTES, LocalAesGcmCredentialCipher
from tabvio.credentials.exceptions import CredentialInvalidError
from tabvio.credentials.models import CreateCredentialRequest, UpdateCredentialRequest
from tabvio.credentials.repository import CredentialRepository
from tabvio.credentials.service import CredentialService


class ContactCredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = CredentialRepository(Path(self.directory.name) / "credentials.db")
        self.repository.initialize()
        self.cipher = LocalAesGcmCredentialCipher(b"k" * KEY_LENGTH_BYTES)
        self.service = CredentialService(self.repository, self.cipher)
        self.owner_id = uuid4()
        self.contact = {
            "email": "ada@example.com", "first_name": "Ada",
            "last_name": "Lovelace", "phone": "+16195550123",
        }
        self.metadata = self.service.create(self.owner_id, CreateCredentialRequest(
            name="Checkout contact", allowed_domains=["shop.example"], **self.contact,
        ))

    def tearDown(self):
        self.directory.cleanup()

    def resolve(self):
        return self.service.resolve_for_domain(self.metadata.id, self.owner_id, "shop.example")

    def test_contact_only_entry_is_encrypted_and_lists_only_field_names(self):
        secret = self.resolve()
        self.assertIsNone(secret.login)
        self.assertIsNone(secret.password)
        self.assertEqual(secret.model_dump(exclude_none=True), self.contact)
        stored = self.repository.get_owned(self.metadata.id, self.owner_id)
        listed = self.service.list(self.owner_id)[0]
        self.assertEqual(listed.available_fields, list(self.contact))
        self.assertEqual(listed.login_hint, "")
        for value in self.contact.values():
            self.assertNotIn(value.encode(), stored.encrypted_payload)
            self.assertNotIn(value, listed.model_dump_json())

    def test_login_can_be_saved_without_password(self):
        metadata = self.service.create(self.owner_id, CreateCredentialRequest(
            name="Passwordless", login="ada", allowed_domains=["shop.example"],
        ))
        self.assertEqual(metadata.available_fields, ["login"])
        secret = self.service.resolve_for_domain(metadata.id, self.owner_id, "shop.example")
        self.assertEqual(secret.login, "ada")
        self.assertIsNone(secret.password)

    def test_updates_preserve_omitted_values_and_clear_explicit_nulls(self):
        self.service.update(self.metadata.id, self.owner_id, UpdateCredentialRequest(
            login="ada-login", password="temporary-password", first_name="  Augusta  ",
        ))
        metadata = self.service.update(self.metadata.id, self.owner_id, UpdateCredentialRequest(
            login=None, password=None, phone=None,
        ))
        secret = self.resolve()
        self.assertEqual(secret.first_name, "Augusta")
        self.assertEqual(secret.email, self.contact["email"])
        self.assertEqual(secret.last_name, self.contact["last_name"])
        self.assertIsNone(secret.login)
        self.assertIsNone(secret.password)
        self.assertIsNone(secret.phone)
        self.assertEqual(metadata.available_fields, ["email", "first_name", "last_name"])
        self.assertEqual(metadata.login_hint, "")

    def test_empty_entry_is_rejected_without_changing_saved_values(self):
        with self.assertRaises(CredentialInvalidError):
            self.service.create(self.owner_id, CreateCredentialRequest(
                name="Empty", email="   ", allowed_domains=["shop.example"],
            ))
        with self.assertRaises(CredentialInvalidError):
            self.service.update(self.metadata.id, self.owner_id, UpdateCredentialRequest(
                email=None, first_name=None, last_name=None, phone=None,
            ))
        self.assertEqual(self.resolve().model_dump(exclude_none=True), self.contact)

    def test_old_encrypted_payload_remains_readable(self):
        stored = self.repository.get_owned(self.metadata.id, self.owner_id)
        associated_data = f"tabvio:credential:v1:{self.owner_id}:{stored.id}".encode()
        stored.encrypted_payload = self.cipher.encrypt(
            b'{"login":"old-user","password":"old-password"}', associated_data,
        )
        stored.available_fields = ["login", "password"]
        self.repository.save(stored)
        secret = self.resolve()
        self.assertEqual(secret.login, "old-user")
        self.assertEqual(secret.password, "old-password")
        self.assertIsNone(secret.email)
