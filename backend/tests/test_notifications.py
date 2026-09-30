"""The requesting engineer is notified when a job ends or needs attention."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import InfraOperationError
from app.models.jobs import JobStatus, StepStatus
from app.models.notifications import Notification
from app.workers import pipeline as pipeline_module
from app.workers.pipeline import ProvisioningPipeline
from app.workers.state_machine import STAGES_BY_KEY
from tests.conftest import make_request

REQUESTER = uuid.UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline_module, "AuditRecorder", lambda db: SimpleNamespace(record=AsyncMock())
    )
    monkeypatch.setattr(pipeline_module, "release_unattended_media", AsyncMock(return_value=False))


def pipeline_context(*, requester: uuid.UUID | None = REQUESTER, final_artifacts: dict | None = None):
    final = SimpleNamespace(
        stage_key="final_validation",
        sequence=24,
        status=StepStatus.SUCCEEDED,
        artifacts=final_artifacts or {},
    )
    waiting = SimpleNamespace(stage_key="configure_guest_network", sequence=12,
                              status=StepStatus.PENDING, output=None)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        vm_name="TEST3",
        requested_by_user_id=requester,
        status=JobStatus.RUNNING,
        started_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=12),
        steps=[waiting, final],
        datacenter_name="DC01",
    )
    db = SimpleNamespace(add=MagicMock(), commit=AsyncMock())
    return SimpleNamespace(
        db=db,
        job=job,
        job_id=job.id,
        vm_name=job.vm_name,
        actor_username="engineer",
        request=make_request(),
        steps_by_key={step.stage_key: step for step in job.steps},
    )


def added_notifications(ctx) -> list[Notification]:
    return [call.args[0] for call in ctx.db.add.call_args_list if isinstance(call.args[0], Notification)]


def pipeline() -> ProvisioningPipeline:
    return ProvisioningPipeline(publisher=SimpleNamespace(publish_stage=AsyncMock()))


@pytest.mark.asyncio
async def test_verified_completion_notifies_the_requester() -> None:
    ctx = pipeline_context(
        final_artifacts={
            "checklist": [
                {"group": "VM", "label": "Exists in vCenter", "status": "PASS"},
                {"group": "Network", "label": "IP address", "status": "PASS"},
            ],
            "summary": {"fqdn": "test3.corp.deltagalil.com", "ip_address": "10.20.30.45"},
        }
    )

    await pipeline()._finalize_success(ctx)

    (note,) = added_notifications(ctx)
    assert note.user_id == REQUESTER
    assert note.job_id == ctx.job.id
    assert note.kind == "JOB_COMPLETED"
    assert note.title == "TEST3 is ready"
    assert "test3.corp.deltagalil.com · 10.20.30.45" in note.message
    assert "All 2 final checks passed" in note.message
    ctx.db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_failed_stage_notifies_the_requester() -> None:
    ctx = pipeline_context()
    stage = STAGES_BY_KEY["wait_for_guest_os"]
    step = SimpleNamespace(stage_key=stage.key, attempt=1)
    failure = InfraOperationError(
        "Windows did not finish its first-boot setup in time.",
        reason="Last observation: Windows Setup is still running.",
        recommended_action="Open the VM console.",
    )

    now = dt.datetime.now(dt.UTC)
    await pipeline()._handle_failure(ctx, stage, step, failure, now, now)

    (note,) = added_notifications(ctx)
    assert note.kind == "JOB_FAILED"
    assert note.title == "TEST3 deployment failed"
    assert "Wait for guest operating system" in note.message
    assert "Windows Setup is still running" in note.message


@pytest.mark.asyncio
async def test_action_required_notifies_the_requester() -> None:
    ctx = pipeline_context()
    stage = STAGES_BY_KEY["wait_for_guest_os"]
    step = SimpleNamespace(stage_key=stage.key)

    await pipeline()._handle_action_required(
        ctx, stage, step, "VMware Tools has no heartbeat.", dt.datetime.now(dt.UTC)
    )

    (note,) = added_notifications(ctx)
    assert note.kind == "JOB_ACTION_REQUIRED"
    assert note.title == "TEST3 needs attention"


@pytest.mark.asyncio
async def test_jobs_without_a_requester_notify_no_one() -> None:
    ctx = pipeline_context(requester=None)

    await pipeline()._finalize_success(ctx)

    assert added_notifications(ctx) == []
