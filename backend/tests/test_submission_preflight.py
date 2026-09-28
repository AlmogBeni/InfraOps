"""submit_job enforces the preflight on the server."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import DomainValidationError
from app.schemas.provisioning import CheckStatus, PreflightCheck, PreflightReport, ProvisioningSubmissionRequest
from app.services.provisioning import service as service_module
from tests.conftest import make_request


@pytest.mark.asyncio
async def test_blocked_preflight_rejects_submission_before_a_job_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    report = PreflightReport(
        ready=False,
        summary="Not ready",
        checks=[
            PreflightCheck(code="vm_name_unique", label="VM name is available", status=CheckStatus.FAIL,
                           detail="A VM named 'SERVER-PROD-042' already exists."),
        ],
    )
    validator = SimpleNamespace(validate=AsyncMock(return_value=report))
    monkeypatch.setattr(service_module, "PreflightValidator", lambda _db, _vmware: validator)
    repo = SimpleNamespace(
        find_by_idempotency_key=AsyncMock(return_value=None),
        has_active_job_for_vm=AsyncMock(return_value=False),
        has_ipv4_reservation=AsyncMock(return_value=False),
        create_job=AsyncMock(),
    )
    monkeypatch.setattr(service_module, "JobRepository", lambda _db: repo)
    monkeypatch.setattr(
        service_module, "AuditRecorder", lambda _db: SimpleNamespace(record=AsyncMock())
    )
    db = SimpleNamespace(commit=AsyncMock())
    request = ProvisioningSubmissionRequest.model_validate(make_request().model_dump(mode="json") | {
        "identity_policy_version": "v2",
        "guest": make_request().guest.model_dump(mode="json") | {"credential_secret_ref": "guest-admin"},
    })

    with pytest.raises(DomainValidationError) as raised:
        await service_module.submit_provisioning(
            db, user=None, request=request, idempotency_key=None, source_ip=None, vmware=object()
        )

    assert raised.value.details["blocking_checks"][0]["code"] == "vm_name_unique"
    repo.create_job.assert_not_awaited()
