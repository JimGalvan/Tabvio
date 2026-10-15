import asyncio
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tabvio_agent.models import CredentialMetadata, CredentialSecret
from tabvio_agent.credentials import CredentialProvider
from tabvio.credentials.agent_provider import CredentialServiceProvider
from tabvio.credentials.cipher import KEY_LENGTH_BYTES, LocalAesGcmCredentialCipher
from tabvio.credentials.exceptions import (
    CredentialDomainDeniedError,
    CredentialNotFoundError,
)
from tabvio.credentials.models import CreateCredentialRequest
from tabvio.credentials.repository import CredentialRepository
from tabvio.credentials.service import CredentialService


class CredentialAgentProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        repository = CredentialRepository(Path(self.directory.name) / "credentials.db")
        repository.initialize()
        self.service = CredentialService(repository, LocalAesGcmCredentialCipher(b"k" * KEY_LENGTH_BYTES))
        self.user_id = uuid4()
        self.values = {
            "login": "ada-login", "password": "saved-password", "email": "ada@example.com",
            "first_name": "Ada", "last_name": "Lovelace", "phone": "+16195550123",
        }
        self.metadata = self.service.create(self.user_id, CreateCredentialRequest(
            name="Shop", allowed_domains=["shop.example", "*.shop.example"],
            is_default=True, preferred_verification=["sms", "email"], **self.values,
        ))
        self.provider: CredentialProvider = CredentialServiceProvider(
            self.service, self.user_id, (self.metadata.id,),
        )

    async def test_lists_only_selected_metadata_in_order_without_duplicates(self):
        other = self.service.create(self.user_id, CreateCredentialRequest(
            name="Other", login="other-login", allowed_domains=["shop.example"],
        ))
        provider = CredentialServiceProvider(
            self.service, self.user_id, (other.id, self.metadata.id, other.id),
        )
        selected = await provider.list_selected()
        self.assertEqual(selected, (
            CredentialMetadata(other.id, "Other", ("shop.example",), other.login_hint, False, ("login",)),
            CredentialMetadata(
                self.metadata.id, "Shop", ("shop.example", "*.shop.example"),
                self.metadata.login_hint, True, tuple(self.values), ("sms", "email"),
            ),
        ))
        for item in selected:
            for value in self.values.values():
                self.assertNotIn(value, repr(item))
        with self.assertRaises(FrozenInstanceError):
            selected[0].name = "Changed"
        names = []
        for item in fields(CredentialMetadata):
            names.append(item.name)
        self.assertEqual(names, [
            "id", "name", "allowed_domains", "login_hint", "is_default",
            "available_fields", "preferred_verification",
        ])

    async def test_empty_selection_lists_nothing_and_refuses_resolution(self):
        provider = CredentialServiceProvider(self.service, self.user_id, ())
        self.assertEqual(await provider.list_selected(), ())
        with self.assertRaisesRegex(ValueError, "^Credential is not selected for this run$"):
            await provider.resolve(self.metadata.id, "shop.example")

    async def test_converts_all_secret_fields_and_hides_them_from_repr(self):
        secret = await self.provider.resolve(self.metadata.id, "shop.example")
        self.assertIsInstance(secret, CredentialSecret)
        for name, value in self.values.items():
            self.assertEqual(getattr(secret, name), value)
            self.assertNotIn(value, repr(secret))
        self.assertEqual(repr(secret), "CredentialSecret()")
        with self.assertRaises(FrozenInstanceError):
            secret.password = "Changed"

    async def test_contact_only_secret_keeps_missing_fields_optional(self):
        contact = self.service.create(self.user_id, CreateCredentialRequest(
            name="Contact", email=self.values["email"], allowed_domains=["shop.example"],
        ))
        provider = CredentialServiceProvider(self.service, self.user_id, (contact.id,))
        self.assertEqual(
            await provider.resolve(contact.id, "shop.example"),
            CredentialSecret(email=self.values["email"]),
        )

    async def test_unselected_credential_is_refused_before_calling_service(self):
        with patch.object(self.service, "resolve_for_domain") as resolve:
            with self.assertRaisesRegex(ValueError, "^Credential is not selected for this run$"):
                await self.provider.resolve(uuid4(), "shop.example")
        resolve.assert_not_called()

    async def test_owner_checks_apply_to_listing_and_resolution(self):
        provider = CredentialServiceProvider(self.service, uuid4(), (self.metadata.id,))
        with self.assertRaisesRegex(CredentialNotFoundError, "^Credential was not found$"):
            await provider.list_selected()
        with self.assertRaisesRegex(CredentialNotFoundError, "^Credential was not found$"):
            await provider.resolve(self.metadata.id, "shop.example")

    async def test_domain_check_preserves_service_error_and_accepts_subdomains(self):
        with self.assertRaises(CredentialDomainDeniedError) as raised:
            await self.provider.resolve(self.metadata.id, "attacker.example")
        self.assertEqual(str(raised.exception), (
            "Credential 'Shop' is not allowed on attacker.example. "
            "Add attacker.example or *.attacker.example to its allowed domains."
        ))
        secret = await self.provider.resolve(self.metadata.id, "checkout.shop.example")
        self.assertEqual(secret.password, self.values["password"])

    async def test_revoked_selected_credential_cannot_be_listed_or_resolved(self):
        self.service.revoke(self.metadata.id, self.user_id)
        with self.assertRaises(CredentialNotFoundError):
            await self.provider.list_selected()
        with self.assertRaises(CredentialNotFoundError):
            await self.provider.resolve(self.metadata.id, "shop.example")

    async def test_service_calls_run_in_worker_threads_with_scoped_arguments(self):
        main_thread = threading.get_ident()
        require_selected = self.service.require_selected
        resolve_for_domain = self.service.resolve_for_domain
        worker_threads = []

        def list_selected(credential_ids, user_id):
            worker_threads.append(threading.get_ident())
            self.assertEqual(credential_ids, (self.metadata.id,))
            self.assertEqual(user_id, self.user_id)
            return require_selected(credential_ids, user_id)

        def resolve(credential_id, user_id, domain):
            worker_threads.append(threading.get_ident())
            self.assertEqual((credential_id, user_id, domain), (
                self.metadata.id, self.user_id, "shop.example",
            ))
            return resolve_for_domain(credential_id, user_id, domain)

        with patch.object(self.service, "require_selected", side_effect=list_selected):
            with patch.object(self.service, "resolve_for_domain", side_effect=resolve):
                await asyncio.gather(
                    self.provider.list_selected(), self.provider.resolve(self.metadata.id, "shop.example"),
                )
        self.assertEqual(len(worker_threads), 2)
        for worker_thread in worker_threads:
            self.assertNotEqual(worker_thread, main_thread)
