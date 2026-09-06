from __future__ import annotations

import pytest

from app.secrets.encryption import decrypt_secret, encrypt_secret


def test_credential_ciphertext_round_trips_without_containing_plaintext() -> None:
    plaintext = "correct-horse-battery-staple"
    ciphertext = encrypt_secret(plaintext)
    assert plaintext not in ciphertext
    assert decrypt_secret(ciphertext) == plaintext


def test_tampered_credential_ciphertext_fails_closed() -> None:
    ciphertext = encrypt_secret("secret")
    with pytest.raises(RuntimeError, match="could not be decrypted"):
        decrypt_secret(ciphertext[:-2] + "xx")
