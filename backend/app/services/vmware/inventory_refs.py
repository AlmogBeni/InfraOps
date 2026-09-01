"""Opaque identifiers for datastore-backed inventory files.

The identifiers are transport tokens, not trust boundaries. Callers must still
revalidate the decoded datastore and path against current vSphere inventory.
"""

from __future__ import annotations

import base64
import json

_ISO_PREFIX = "iso:"


def encode_iso_id(datastore_id: str, path: str) -> str:
    payload = json.dumps([datastore_id, path], separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{_ISO_PREFIX}{token}"


def decode_iso_id(value: str) -> tuple[str, str]:
    if not value.startswith(_ISO_PREFIX):
        raise ValueError("Unknown ISO identifier format.")
    token = value[len(_ISO_PREFIX):]
    token += "=" * (-len(token) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid ISO identifier.") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 2
        or not all(isinstance(item, str) and item for item in decoded)
    ):
        raise ValueError("Invalid ISO identifier payload.")
    return decoded[0], decoded[1]
