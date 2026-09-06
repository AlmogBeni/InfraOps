"""Validation tests for administrator-managed infrastructure references."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.admin import SecretReferenceCreate, VCenterConnectionCreate


def vcenter_payload(username_ref: str, password_ref: str) -> dict:
    return {
        "name": "Production vCenter",
        "host": "vcsa.example.com",
        "username_secret_ref": username_ref,
        "password_secret_ref": password_ref,
    }


def test_vcenter_accepts_slash_separated_secret_references() -> None:
    connection = VCenterConnectionCreate.model_validate(
        vcenter_payload("vcenter/username", "vcenter/password")
    )

    assert connection.username_secret_ref == "vcenter/username"
    assert connection.password_secret_ref == "vcenter/password"


@pytest.mark.parametrize(
    "invalid_ref",
    [
        "/vcenter/username",
        "vcenter/username/",
        "vcenter//username",
        "vcenter/./username",
        "vcenter/../username",
        "VCenter/username",
    ],
)
def test_secret_reference_rejects_unsafe_path_forms(invalid_ref: str) -> None:
    with pytest.raises(ValidationError):
        SecretReferenceCreate(name=invalid_ref, username="user", password="password")


def test_secret_reference_preserves_150_character_limit() -> None:
    assert len(SecretReferenceCreate(name="a" * 150, username="user", password="password").name) == 150
    with pytest.raises(ValidationError):
        SecretReferenceCreate(name="a" * 151, username="user", password="password")
