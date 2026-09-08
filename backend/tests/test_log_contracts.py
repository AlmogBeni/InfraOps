"""Structured log projection and technical-detail masking tests."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.v1.audit import _audit_out, _public_details
from app.api.v1.provisioning import _step_out
from app.audit.actions import audit_action_label
from app.models.audit import AuditEvent
from app.models.jobs import ProvisioningJob, ProvisioningJobStep, StepStatus
from app.repositories.audit import normalized_action_term
from app.repositories.logs import (
    LOGGABLE_STEP_STATUSES,
    details_for_step,
    message_for_step,
    severity_for_status,
)
from app.schemas.provisioning import VmSourceType
from app.services.vmware.base import PowerStateInfo
from app.workers.stages import stage_final_validation


def _step(status: StepStatus = StepStatus.FAILED) -> ProvisioningJobStep:
    return ProvisioningJobStep(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        stage_key="clone_vm",
        name="Deploy OVF/OVA package",
        sequence=4,
        status=status,
        attempt=1,
        max_attempts=3,
        retryable=False,
        started_at=dt.datetime.now(dt.UTC),
        finished_at=dt.datetime.now(dt.UTC),
        output=None,
        error_human="Deployment was rejected.",
        error_technical="vCenter stack trace and internal endpoint",
        artifacts={"safe": True},
    )


def test_job_step_technical_detail_is_admin_only() -> None:
    step = _step()
    assert _step_out(step).error_technical is None
    assert _step_out(step, include_technical=True).error_technical == step.error_technical


def test_log_severity_and_message_are_human_readable() -> None:
    step = _step()
    assert severity_for_status(step.status) == "ERROR"
    assert message_for_step(step) == "Deployment was rejected."
    assert severity_for_status(StepStatus.CANCELLED) == "WARNING"
    assert severity_for_status(StepStatus.WARNING) == "WARNING"
    assert severity_for_status(StepStatus.WAITING_FOR_PREREQUISITE) == "WARNING"
    assert severity_for_status(StepStatus.SKIPPED) == "DEBUG"
    assert severity_for_status(StepStatus.NOT_APPLICABLE) == "DEBUG"


def test_log_message_uses_only_a_concise_first_line() -> None:
    step = _step(StepStatus.SUCCEEDED)
    step.output = "Hardware configured successfully.\nCPU: 4\n" + "x" * 500
    assert message_for_step(step) == "Hardware configured successfully."


def test_pending_future_stages_are_not_log_events() -> None:
    assert StepStatus.PENDING not in LOGGABLE_STEP_STATUSES
    assert StepStatus.RUNNING in LOGGABLE_STEP_STATUSES
    assert StepStatus.SUCCEEDED in LOGGABLE_STEP_STATUSES
    assert StepStatus.WARNING in LOGGABLE_STEP_STATUSES
    assert StepStatus.WAITING_FOR_PREREQUISITE in LOGGABLE_STEP_STATUSES
    assert StepStatus.NOT_APPLICABLE in LOGGABLE_STEP_STATUSES


def test_non_admin_log_details_exclude_internal_ids_and_artifacts() -> None:
    step = _step()
    job = ProvisioningJob(
        id=uuid.uuid4(),
        vm_name="SERVER-101",
        datacenter_id="datacenter-21",
        datacenter_name="Production",
    )
    public = details_for_step(step, job, include_technical=False)
    admin = details_for_step(step, job, include_technical=True)
    assert public == {"stage": "clone_vm", "status": "FAILED", "attempt": 1}
    assert admin["datacenter_id"] == "datacenter-21"
    assert admin["artifacts"] == {"safe": True}
    assert "technical_error" in admin


def test_non_admin_audit_details_exclude_internal_ids_recursively() -> None:
    details = {
        "source_type": "template",
        "vm_id": "vm-101",
        "artifacts": {"secret_id": "internal"},
        "nested": {"cluster_id": "domain-c7", "attempt": 2},
    }
    assert _public_details(details) == {
        "source_type": "template",
        "nested": {"attempt": 2},
    }


def test_non_admin_audit_output_keeps_human_summary() -> None:
    event = AuditEvent(
        id=uuid.uuid4(),
        timestamp=dt.datetime.now(dt.UTC),
        action="VM_CREATED",
        details={"vm_id": "vm-101", "source_type": "blank"},
        detail_text="Blank virtual machine created successfully.",
    )

    output = _audit_out(event, "Production", include_technical=False)

    assert output.detail_text == "Blank virtual machine created successfully."
    assert output.details == {"source_type": "blank"}


def test_audit_action_label_expands_backend_identifier() -> None:
    assert audit_action_label("VM_CREATED") == "VM Created"
    assert audit_action_label("IP_CONFLICT_CHECK_PERFORMED") == "IP Conflict Check Performed"


def test_audit_search_normalizes_human_action_labels() -> None:
    assert normalized_action_term("VM created") == "VM_CREATED"
    assert normalized_action_term("IP conflict-check performed") == "IP_CONFLICT_CHECK_PERFORMED"


async def test_blank_final_validation_uses_network_name_not_inventory_id() -> None:
    ctx = SimpleNamespace(
        vmware=SimpleNamespace(
            get_vm_info=AsyncMock(
                return_value=PowerStateInfo(power_state="poweredOff")
            )
        ),
        target=object(),
        vm_name="BLANK-001",
        request=SimpleNamespace(
            source_type=VmSourceType.BLANK,
            network=SimpleNamespace(network_id="dvportgroup-51"),
        ),
        steps_by_key={
            "validate_infrastructure": SimpleNamespace(
                artifacts={"network_name": "VLAN100-PROD"}
            )
        },
    )

    outcome = await stage_final_validation(ctx)
    checklist = outcome.artifacts["checklist"]
    network_check = next(item for item in checklist if item["group"] == "Network")
    assert network_check["detail"] == "VLAN100-PROD"
    assert "dvportgroup-51" not in str(outcome.artifacts)
