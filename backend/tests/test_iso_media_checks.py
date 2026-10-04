"""Windows Server media and product-key preflight, legacy jobs, console screenshots."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import DomainValidationError, InfraOperationError, NotFoundError
from app.models.jobs import JobStatus, StepStatus
from app.schemas.provisioning import CheckStatus
from app.secrets.base import SecretNotFoundError
from app.services.provisioning.preflight import PreflightValidator
from app.services.vmware.mock import mock_iso_header
from app.services.windows_media import (
    ISO_HEADER_BYTES,
    WindowsMediaKind,
    classify_windows_media,
    iso_volume_label,
)
from tests.conftest import make_request


@pytest.mark.parametrize(
    ("label", "kind"),
    [
        ("SSS_X64FREE_EN-US_DV9", WindowsMediaKind.SERVER),
        ("SSS_X64FRE_EN-US_DV9", WindowsMediaKind.SERVER),
        ("IR3_SSS_X64FREV_EN-US_DV9", WindowsMediaKind.SERVER),
        ("CCCOMA_X64FRE_EN-US_DV9", WindowsMediaKind.CLIENT),
        ("J_CCSA_X64FRE_EN-US_DV5", WindowsMediaKind.CLIENT),
        ("CPBA_X64FRE_EN-US_DV5", WindowsMediaKind.CLIENT),
        ("ESD-ISO", WindowsMediaKind.CLIENT),
        ("Ubuntu-Server 24.04 LTS amd64", WindowsMediaKind.UNKNOWN),
        (None, WindowsMediaKind.UNKNOWN),
    ],
)
def test_windows_media_is_classified_by_volume_label(label, kind) -> None:
    assert classify_windows_media(label) == kind


def test_volume_label_is_read_from_the_primary_volume_descriptor() -> None:
    header = mock_iso_header("SSS_X64FREE_EN-US_DV9")
    assert len(header) == ISO_HEADER_BYTES
    assert iso_volume_label(header) == "SSS_X64FREE_EN-US_DV9"
    assert iso_volume_label(b"\x00" * ISO_HEADER_BYTES) is None
    assert iso_volume_label(b"too short") is None


def _validator(*, header: bytes | Exception = b"", secret: str | Exception = "") -> PreflightValidator:
    read = AsyncMock(side_effect=header) if isinstance(header, Exception) else AsyncMock(return_value=header)
    get_secret = AsyncMock(side_effect=secret) if isinstance(secret, Exception) else AsyncMock(return_value=secret)
    return PreflightValidator(
        db=None,
        vmware=SimpleNamespace(read_datastore_file=read),
        secrets=SimpleNamespace(get_secret=get_secret),
    )


IMAGE = SimpleNamespace(name="Windows Server 2025.iso", path="[PROD-SAN-01] ISO/Windows Server 2025.iso")


async def _media_check(validator: PreflightValidator):
    checks = []
    await validator._check_windows_server_media(
        object(), "datacenter-21", IMAGE, lambda *args, **kwargs: checks.append(args)
    )
    (check,) = checks
    return check


async def test_windows_server_media_passes_preflight() -> None:
    code, _label, status, detail = await _media_check(_validator(header=mock_iso_header("SSS_X64FREE_EN-US_DV9")))
    assert (code, status) == ("windows_media", CheckStatus.PASS)
    assert "SSS_X64FREE_EN-US_DV9" in detail


async def test_windows_client_media_is_blocked() -> None:
    _code, _label, status, detail = await _media_check(
        _validator(header=mock_iso_header("CCCOMA_X64FRE_EN-US_DV9"))
    )
    assert status == CheckStatus.FAIL
    assert "client media" in detail


async def test_unrecognised_media_is_blocked_with_guidance() -> None:
    _code, _label, status, detail = await _media_check(_validator(header=b"\x00" * ISO_HEADER_BYTES))
    assert status == CheckStatus.FAIL
    assert "SSS_" in detail and "volume label missing" in detail


async def test_unreadable_media_is_blocked() -> None:
    failure = InfraOperationError("Download refused", reason="403", recommended_action="Grant Datastore > Browse")
    _code, _label, status, detail = await _media_check(_validator(header=failure))
    assert status == CheckStatus.FAIL
    assert "Grant Datastore > Browse" in detail


async def _product_key_check(validator: PreflightValidator, reference: str | None):
    request = make_request()
    request.guest.product_key_secret_ref = reference
    checks = []
    await validator._check_product_key(request, lambda *args, **kwargs: checks.append(args))
    (check,) = checks
    return check


@pytest.mark.parametrize(
    ("reference", "secret", "status"),
    [
        (None, "", CheckStatus.PASS),
        ("windows-key", "abcde-fghij-klmno-pqrst-uvwxy\n", CheckStatus.PASS),
        ("windows-key", "not-a-product-key", CheckStatus.FAIL),
        ("windows-key", SecretNotFoundError("windows-key/password", "database"), CheckStatus.FAIL),
    ],
)
async def test_product_key_is_checked_before_submission(reference, secret, status) -> None:
    validator = _validator(secret=secret)
    _code, _label, observed, detail = await _product_key_check(validator, reference)
    assert observed == status
    if reference:
        validator._secrets.get_secret.assert_awaited_once_with("windows-key/password")
    # The key itself is a secret and never appears in a check.
    assert "ABCDE" not in detail.upper()


async def test_jobs_of_removed_workflows_cannot_be_retried() -> None:
    from app.services.provisioning.service import retry_stages

    payload = make_request().model_dump(mode="json")
    payload["source_type"] = "template"
    payload["guest"] = {"template_id": "corp-windows-2025", "iso_id": None}
    job = SimpleNamespace(
        status=JobStatus.FAILED,
        request=SimpleNamespace(payload=payload),
        steps=[SimpleNamespace(stage_key="create_vm", status=StepStatus.FAILED)],
    )

    with pytest.raises(DomainValidationError, match="can only be viewed") as raised:
        await retry_stages(None, user=None, job=job, stage_key=None, source_ip=None)

    assert raised.value.details == {"reason": "legacy_request"}


async def _screenshot(monkeypatch, path, *, read=b"\x89PNG"):
    from app.api.v1 import provisioning as api

    request = make_request()
    job = SimpleNamespace(
        datacenter_id="datacenter-21",
        request=SimpleNamespace(payload=request.model_dump(mode="json")),
        steps=[SimpleNamespace(stage_key="wait_for_guest_os", artifacts={"console_screenshot": path})],
    )
    vmware = SimpleNamespace(read_datastore_file=AsyncMock(return_value=read))
    monkeypatch.setattr(api, "_get_job", AsyncMock(return_value=job))
    monkeypatch.setattr(api, "build_vcenter_target", AsyncMock(return_value="target"))
    monkeypatch.setattr(api, "get_vmware_service", lambda: vmware)
    response = await api.get_console_screenshot(uuid.uuid4(), "wait_for_guest_os", db=None, user=None)
    return response, vmware


async def test_console_screenshot_is_streamed_from_the_datastore(monkeypatch) -> None:
    response, vmware = await _screenshot(monkeypatch, "[PROD-SAN-01] SERVER-PROD-042/SERVER-PROD-042-1.png")

    assert response.body == b"\x89PNG"
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "no-store"
    args = vmware.read_datastore_file.await_args
    assert args.args[1:] == ("datacenter-21", "[PROD-SAN-01] SERVER-PROD-042/SERVER-PROD-042-1.png")


@pytest.mark.parametrize("path", [None, "[ds] ../../etc/secret.png", "[ds] vm/notes.txt", "C:/x.png"])
async def test_only_recorded_screenshots_are_served(monkeypatch, path) -> None:
    with pytest.raises(NotFoundError):
        await _screenshot(monkeypatch, path)


def test_console_screenshots_are_administrator_only() -> None:
    from app.api.v1.provisioning import router
    from app.auth.permissions import Permission

    route = next(route for route in router.routes if route.path.endswith("/console-screenshot"))
    required = {
        cell.cell_contents
        for dependency in route.dependant.dependencies
        for cell in (getattr(dependency.call, "__closure__", None) or ())
        if isinstance(cell.cell_contents, Permission)
    }
    assert required == {Permission.ADMIN_SETTINGS}
