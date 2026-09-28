"""Authenticated encryption for credentials stored by InfraOps.

Keys are independent of the JWT signing key so each can be rotated on its own:

* ``CREDENTIAL_ENCRYPTION_KEY`` — root of the key that encrypts new values.
  When unset, the legacy behaviour applies and the key is derived from
  ``SECRET_KEY``.
* ``CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`` — comma-separated former roots that
  are still accepted for decryption. ``SECRET_KEY`` is always accepted as a
  decryption fallback so existing ciphertext keeps working after a dedicated
  key is introduced.

After changing the encryption key, run ``python -m app.secrets.rotate`` to
re-encrypt every stored credential under the current key; the previous keys
can then be removed. Ciphertexts are never returned through API responses.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings
from app.secrets.base import SecretsProviderError

_KEY_LABEL = b"infraops/credential-store/v1\0"


def _derive(root: str) -> Fernet:
    derived = hashlib.sha256(_KEY_LABEL + root.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _key_roots() -> list[str]:
    settings = get_settings()
    primary = settings.credential_encryption_key or settings.secret_key
    roots = [primary]
    for previous in settings.credential_encryption_previous_keys.split(","):
        previous = previous.strip()
        if previous and previous not in roots:
            roots.append(previous)
    if settings.secret_key and settings.secret_key not in roots:
        roots.append(settings.secret_key)
    return roots


def _fernet() -> MultiFernet:
    return MultiFernet([_derive(root) for root in _key_roots()])


def encrypt_secret(value: str) -> str:
    if not value:
        raise ValueError("A secret value cannot be empty.")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SecretsProviderError(
            "A stored credential could not be decrypted. Verify CREDENTIAL_ENCRYPTION_KEY "
            "(or SECRET_KEY) and CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS."
        ) from exc


def reencrypt_secret(value: str) -> str:
    """Re-encrypt a ciphertext under the current primary key."""
    try:
        return _fernet().rotate(value.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SecretsProviderError("A stored credential could not be re-encrypted.") from exc
