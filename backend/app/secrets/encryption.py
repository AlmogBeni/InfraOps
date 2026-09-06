"""Authenticated encryption for credentials stored by InfraOps.

The application SECRET_KEY is intentionally the only root secret required at
runtime. A purpose-separated Fernet key is derived from it; ciphertexts are
safe to persist in PostgreSQL and are never returned through API responses.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.secrets.base import SecretsProviderError


def _fernet() -> Fernet:
    root = get_settings().secret_key.encode("utf-8")
    derived = hashlib.sha256(b"infraops/credential-store/v1\0" + root).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str) -> str:
    if not value:
        raise ValueError("A secret value cannot be empty.")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SecretsProviderError(
            "A stored credential could not be decrypted. Verify that SECRET_KEY has not changed."
        ) from exc
