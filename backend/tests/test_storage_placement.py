"""Automatic storage placement follows the administrators' preferred datastores."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.admin import PlatformSettingsUpdate
from app.schemas.infrastructure import DatastoreOut
from app.schemas.provisioning import CheckStatus
from app.services.provisioning.placement import choose_automatic_datastore, normalize_preferred
from app.services.provisioning.preflight import PreflightValidator
from tests.conftest import make_request


def _datastore(id_: str, name: str, free_gb: float, accessible: bool = True) -> DatastoreOut:
    return DatastoreOut(id=id_, name=name, type="VMFS", capacity_gb=4000, free_gb=free_gb,
                        usage_percent=50, accessible=accessible)


DATASTORES = {
    entry.id: entry
    for entry in (
        _datastore("ds-slow", "CAPACITY-NFS", 9000),
        _datastore("ds-fast", "ALLFLASH-01", 1200),
        _datastore("ds-offline", "ALLFLASH-03", 5000, accessible=False),
    )
}


def _storage_checks(preferred: list[str], *, selected: str | None = None, disk_gb: int = 100) -> list[tuple]:
    request = make_request()
    request.hardware.disks[0].size_gb = disk_gb
    request.hardware.disks[0].datastore_id = selected
    checks: list[tuple] = []
    PreflightValidator._check_storage(
        request, DATASTORES, {"preferred_datastores": preferred}, lambda *args, **kwargs: checks.append((args, kwargs))
    )
    return checks


def test_preferred_list_is_cleaned_in_priority_order() -> None:
    assert normalize_preferred([" ALLFLASH-01 ", "", "allflash-01", "SSD-02"]) == ["ALLFLASH-01", "SSD-02"]


def test_preferred_datastore_is_used_even_when_another_has_more_room() -> None:
    choice = choose_automatic_datastore(DATASTORES.values(), ["ALLFLASH-03", "ALLFLASH-01"], 500)

    assert choice.datastore.id == "ds-fast"
    assert choice.detail == "ALLFLASH-01 (preferred datastore 2 of 2, 1200 GB free)"


def test_preflight_shows_the_automatic_choice() -> None:
    ((args, _),) = _storage_checks(["ALLFLASH-01"])

    assert args[:3] == ("datastore_capacity", "Automatic storage placement", CheckStatus.PASS)
    assert args[3].startswith("ALLFLASH-01 (preferred datastore 1 of 1")


def test_preflight_blocks_when_no_preferred_datastore_has_room() -> None:
    ((args, kwargs),) = _storage_checks(["ALLFLASH-01", "ALLFLASH-03"], disk_gb=2000)

    assert args[2] == CheckStatus.FAIL
    assert "ALLFLASH-01 has 1200 GB free; ALLFLASH-03 is not accessible" in args[3]
    assert args[3].endswith("or add one under Settings → Preferred datastores.")
    assert kwargs.get("blocking", True) is True


def test_preflight_warns_while_no_preferred_datastores_are_configured() -> None:
    (placement, warning) = _storage_checks([])

    assert placement[0][2] == CheckStatus.PASS
    assert placement[0][3].startswith("CAPACITY-NFS")
    assert warning[0][:3] == ("preferred_datastores", "Preferred datastores configured", CheckStatus.WARN)
    assert warning[1] == {"blocking": False}


def test_a_selected_datastore_must_have_room_for_every_disk() -> None:
    ((args, _),) = _storage_checks(["ALLFLASH-01"], selected="ds-fast", disk_gb=1500)

    assert args[:3] == ("datastore_capacity", "Selected datastore capacity", CheckStatus.FAIL)
    assert "ALLFLASH-01 has 1200 GB free" in args[3]


def test_settings_accept_and_clean_preferred_datastores() -> None:
    update = PlatformSettingsUpdate(preferred_datastores=[" ALLFLASH-01", "allflash-01", "SSD 02 "])
    assert update.preferred_datastores == ["ALLFLASH-01", "SSD 02"]

    with pytest.raises(ValidationError, match="at most 80 characters"):
        PlatformSettingsUpdate(preferred_datastores=["x" * 81])


def test_settings_output_includes_preferred_datastores() -> None:
    from app.api.v1.admin_platform import _settings_out

    assert _settings_out({"preferred_datastores": ["ALLFLASH-01"]}).preferred_datastores == ["ALLFLASH-01"]
    assert _settings_out({}).preferred_datastores == []
