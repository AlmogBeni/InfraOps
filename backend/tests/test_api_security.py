"""Security regressions for infrastructure and error-response contracts."""

from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.audit import router as audit_router
from app.api.v1.infrastructure import router as infrastructure_router
from app.api.v1.logs import router as logs_router
from app.core.errors import InfraOperationError, register_exception_handlers
from app.models.jobs import JobStatus, JobType
from app.schemas.jobs import job_out
from app.services.provisioning.preflight import _safe_infrastructure_failure


class _DashboardResult:
    def __init__(self, *, scalar=0, rows=None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


def test_infrastructure_router_has_a_router_wide_auth_dependency() -> None:
    assert infrastructure_router.dependencies
    assert all(route.dependencies for route in infrastructure_router.routes)


def test_infrastructure_error_response_masks_technical_detail() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/failure")
    async def failure():
        raise InfraOperationError(
            "vCenter inventory could not be loaded.",
            reason="The upstream service rejected the request.",
            recommended_action="Verify the connection and retry.",
            technical_detail="password=do-not-expose stack trace",
            retryable=True,
        )

    response = TestClient(app, raise_server_exceptions=False).get("/failure")
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "infrastructure_operation_failed"
    assert body["error"]["details"]["retryable"] is True
    assert "do-not-expose" not in response.text


def test_audit_and_log_filters_accept_frontend_datacenter_key() -> None:
    app = FastAPI()
    app.include_router(audit_router)
    app.include_router(logs_router)
    schema = app.openapi()
    for path in ("/audit", "/logs"):
        parameters = schema["paths"][path]["get"]["parameters"]
        assert "datacenter" in {parameter["name"] for parameter in parameters}


def test_network_and_iso_discovery_require_datacenter_scope() -> None:
    app = FastAPI()
    app.include_router(infrastructure_router)
    schema = app.openapi()
    for path in ("/infrastructure/networks", "/infrastructure/isos"):
        parameters = schema["paths"][path]["get"]["parameters"]
        datacenter = next(item for item in parameters if item["name"] == "datacenter_id")
        assert datacenter["required"] is True


def test_preflight_failure_text_does_not_include_technical_detail() -> None:
    error = InfraOperationError(
        "Inventory could not be loaded.",
        reason="The service rejected the request.",
        recommended_action="Retry after checking permissions.",
        technical_detail="password=do-not-expose traceback",
    )
    detail = _safe_infrastructure_failure(error, "fallback")
    assert "Inventory could not be loaded" in detail
    assert "do-not-expose" not in detail


def test_job_response_includes_durable_datacenter_context() -> None:
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type=JobType.VM_PROVISIONING,
        status=JobStatus.QUEUED,
        vm_name="APP-001",
        datacenter_id="datacenter-21",
        datacenter_name="DC01-Corporate",
        requested_by_user_id=None,
        current_stage=None,
        progress=0,
        error_summary=None,
        cancel_requested=False,
        queued_at=None,
        started_at=None,
        finished_at=None,
        duration_seconds=None,
    )

    response = job_out(job)

    assert response.datacenter_id == "datacenter-21"
    assert response.datacenter_name == "DC01-Corporate"
    assert response.infrastructure_status == "PENDING"
    assert response.guest_os_status == "UNKNOWN"
    assert response.vmware_tools_status == "UNKNOWN"
    assert response.guest_provisioning_status == "PENDING"


@pytest.mark.asyncio
async def test_dashboard_recent_jobs_use_complete_job_contract(monkeypatch) -> None:
    dashboard_module = importlib.import_module("app.api.v1.dashboard")
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type=JobType.VM_PROVISIONING,
        status=JobStatus.ACTION_REQUIRED,
        vm_name="APP-002",
        datacenter_id="datacenter-21",
        datacenter_name="DC01-Corporate",
        requested_by_user_id=None,
        current_stage="wait_for_guest_os",
        progress=40,
        infrastructure_status="READY",
        guest_os_status="INSTALLATION_REQUIRED",
        vmware_tools_status="NOT_APPLICABLE_YET",
        guest_provisioning_status="WAITING_FOR_OS",
        action_required="Install and boot an operating system.",
        error_summary=None,
        cancel_requested=False,
        queued_at=None,
        started_at=None,
        finished_at=None,
        duration_seconds=None,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _DashboardResult(scalar=0),
                _DashboardResult(scalar=0),
                _DashboardResult(scalar=0),
                _DashboardResult(scalar=0),
                _DashboardResult(scalar=0),
                _DashboardResult(rows=[job]),
                _DashboardResult(rows=[]),
            ]
        )
    )
    secrets = SimpleNamespace(
        provider_name="test",
        healthcheck=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(dashboard_module, "get_secrets_service", lambda: secrets)

    response = await dashboard_module.dashboard(db, user=SimpleNamespace())

    assert response.recent_jobs[0].status == JobStatus.ACTION_REQUIRED
    assert response.recent_jobs[0].infrastructure_status == "READY"
    assert response.recent_jobs[0].guest_os_status == "INSTALLATION_REQUIRED"
