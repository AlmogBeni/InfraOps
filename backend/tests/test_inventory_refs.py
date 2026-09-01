"""Opaque inventory reference validation tests."""

from __future__ import annotations

import pytest

from app.schemas.infrastructure import IsoImageOut, TemplateOut
from app.schemas.logs import LogEventOut, LogListResponse
from app.services.vmware.inventory_refs import decode_iso_id, encode_iso_id


def test_frontend_facing_inventory_and_log_shapes_are_stable() -> None:
    assert set(TemplateOut.model_fields) == {
        "id",
        "name",
        "type",
        "description",
        "datacenter_id",
        "datacenter_name",
        "storage_name",
        "location",
        "size_bytes",
        "last_modified",
    }
    assert set(IsoImageOut.model_fields) == {
        "id",
        "name",
        "datacenter_id",
        "datacenter_name",
        "datastore_id",
        "datastore_name",
        "path",
        "size_bytes",
        "last_modified",
    }
    assert set(LogEventOut.model_fields) == {
        "id",
        "timestamp",
        "severity",
        "component",
        "message",
        "resource_name",
        "datacenter_name",
        "job_id",
        "details",
    }
    assert set(LogListResponse.model_fields) == {"items", "total", "page", "page_size"}


def test_iso_reference_roundtrip() -> None:
    encoded = encode_iso_id("datastore-41", "[PROD-SAN-01] ISO/Windows Server.iso")
    assert encoded.startswith("iso:")
    assert decode_iso_id(encoded) == (
        "datastore-41",
        "[PROD-SAN-01] ISO/Windows Server.iso",
    )


@pytest.mark.parametrize(
    "value",
    ["", "datastore-41", "iso:not-base64", "iso:e30"],
)
def test_invalid_iso_references_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        decode_iso_id(value)
