import base64
import os
import unittest
from unittest import mock

from tabvio.config import read_credential_encryption_key
from tabvio.credentials.cipher import (
    KEY_LENGTH_BYTES,
    LocalAesGcmCredentialCipher,
    UnavailableCredentialCipher,
)
from tabvio.credentials.exceptions import CredentialConfigurationError


class LocalAesGcmCredentialCipherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._cipher = LocalAesGcmCredentialCipher(b"k" * KEY_LENGTH_BYTES)
        self._associated_data = b"tabvio:credential:v1:owner:credential"

    def test_round_trips_a_payload(self) -> None:
        sealed = self._cipher.encrypt(b"correct horse", self._associated_data)

        self.assertNotIn(b"correct horse", sealed)
        self.assertTrue(sealed.startswith(b"v1:"))
        self.assertEqual(
            b"correct horse", self._cipher.decrypt(sealed, self._associated_data)
        )

    def test_encrypting_twice_produces_different_ciphertext(self) -> None:
        first = self._cipher.encrypt(b"correct horse", self._associated_data)
        second = self._cipher.encrypt(b"correct horse", self._associated_data)

        self.assertNotEqual(first, second)

    def test_rejects_payload_bound_to_other_associated_data(self) -> None:
        sealed = self._cipher.encrypt(b"correct horse", self._associated_data)

        with self.assertRaises(CredentialConfigurationError):
            self._cipher.decrypt(sealed, b"tabvio:credential:v1:thief:credential")

    def test_rejects_tampered_payload(self) -> None:
        sealed = bytearray(self._cipher.encrypt(b"correct horse", self._associated_data))
        sealed[-1] ^= 0xFF

        with self.assertRaises(CredentialConfigurationError):
            self._cipher.decrypt(bytes(sealed), self._associated_data)

    def test_rejects_payload_written_by_another_key(self) -> None:
        sealed = LocalAesGcmCredentialCipher(b"other key".ljust(KEY_LENGTH_BYTES, b"!")).encrypt(
            b"correct horse", self._associated_data
        )

        with self.assertRaises(CredentialConfigurationError):
            self._cipher.decrypt(sealed, self._associated_data)

    def test_rejects_payload_written_under_another_key_id(self) -> None:
        sealed = LocalAesGcmCredentialCipher(b"k" * KEY_LENGTH_BYTES, key_id=b"v2").encrypt(
            b"correct horse", self._associated_data
        )

        with self.assertRaises(CredentialConfigurationError):
            self._cipher.decrypt(sealed, self._associated_data)

    def test_rejects_a_key_of_the_wrong_length(self) -> None:
        with self.assertRaises(ValueError):
            LocalAesGcmCredentialCipher(b"too short")

    def test_rejects_a_key_id_containing_the_separator(self) -> None:
        with self.assertRaises(ValueError):
            LocalAesGcmCredentialCipher(b"k" * KEY_LENGTH_BYTES, key_id=b"v:1")


class UnavailableCredentialCipherTests(unittest.TestCase):
    def test_refuses_both_directions(self) -> None:
        cipher = UnavailableCredentialCipher()

        with self.assertRaises(CredentialConfigurationError):
            cipher.encrypt(b"secret", b"aad")
        with self.assertRaises(CredentialConfigurationError):
            cipher.decrypt(b"sealed", b"aad")


class ReadCredentialEncryptionKeyTests(unittest.TestCase):
    def test_returns_none_when_unset(self) -> None:
        with mock.patch.dict(os.environ, {"TABVIO_CREDENTIAL_KEY": ""}):
            self.assertIsNone(read_credential_encryption_key())

    def test_decodes_a_generated_key(self) -> None:
        key = os.urandom(KEY_LENGTH_BYTES)
        encoded = base64.b64encode(key).decode()

        with mock.patch.dict(os.environ, {"TABVIO_CREDENTIAL_KEY": encoded}):
            self.assertEqual(key, read_credential_encryption_key())

    def test_rejects_a_value_that_is_not_base64(self) -> None:
        with mock.patch.dict(os.environ, {"TABVIO_CREDENTIAL_KEY": "not base64!"}):
            with self.assertRaises(RuntimeError):
                read_credential_encryption_key()

    def test_rejects_a_key_of_the_wrong_length(self) -> None:
        encoded = base64.b64encode(os.urandom(16)).decode()

        with mock.patch.dict(os.environ, {"TABVIO_CREDENTIAL_KEY": encoded}):
            with self.assertRaises(RuntimeError):
                read_credential_encryption_key()


if __name__ == "__main__":
    unittest.main()
