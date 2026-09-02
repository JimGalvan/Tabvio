from __future__ import annotations

import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tabvio.credentials.exceptions import CredentialConfigurationError

KEY_LENGTH_BYTES = 32
NONCE_LENGTH_BYTES = 12
CURRENT_KEY_ID = b"v1"
KEY_ID_SEPARATOR = b":"


class CredentialCipher(Protocol):
    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes, associated_data: bytes) -> bytes: ...


class LocalAesGcmCredentialCipher:
    """Encrypt small credential payloads with one AES-256-GCM key held locally.

    Each payload is stored as ``<key id>:<nonce><ciphertext+tag>``. The key id
    is what makes rotation possible later: a second key can be introduced under
    a new id while old payloads stay readable through their own id.
    """

    def __init__(self, master_key: bytes, key_id: bytes = CURRENT_KEY_ID):
        if len(master_key) != KEY_LENGTH_BYTES:
            raise ValueError(f"Credential encryption key must be {KEY_LENGTH_BYTES} bytes")
        if not key_id or KEY_ID_SEPARATOR in key_id:
            raise ValueError("Credential key id must be non-empty and contain no colon")
        self._cipher = AESGCM(master_key)
        self._key_id = key_id

    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        nonce = os.urandom(NONCE_LENGTH_BYTES)
        sealed = self._cipher.encrypt(nonce, plaintext, associated_data)
        return self._key_id + KEY_ID_SEPARATOR + nonce + sealed

    def decrypt(self, ciphertext: bytes, associated_data: bytes) -> bytes:
        key_id, separator, body = bytes(ciphertext).partition(KEY_ID_SEPARATOR)
        if not separator or key_id != self._key_id:
            raise CredentialConfigurationError(
                "Saved credential was encrypted with a key this deployment does not have"
            )
        nonce, sealed = body[:NONCE_LENGTH_BYTES], body[NONCE_LENGTH_BYTES:]
        try:
            return self._cipher.decrypt(nonce, sealed, associated_data)
        except InvalidTag as exception:
            raise CredentialConfigurationError(
                "Saved credential could not be decrypted with the configured key"
            ) from exception


class UnavailableCredentialCipher:
    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        raise self._error()

    def decrypt(self, ciphertext: bytes, associated_data: bytes) -> bytes:
        raise self._error()

    @staticmethod
    def _error() -> CredentialConfigurationError:
        return CredentialConfigurationError(
            "Credential storage is unavailable until TABVIO_CREDENTIAL_KEY is configured"
        )
