"""Answer media (plaintext password) never outlives a stopped job."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.jobs import StepStatus
from app.workers.stages import release_unattended_media
from tests.test_vm_deployment_lifecycle import request_for


def media_context(power_on_status: StepStatus, *, cleanup_status=StepStatus.PENDING, removed=False):
    prepare = SimpleNamespace(
        stage_key="prepare_unattended_install",
        status=StepStatus.SUCCEEDED,
        artifacts={"datastore_path": "[ds] infraops-unattend/x.flp", **({"media_removed": True} if removed else {})},
        output="",
    )
    steps = {
        "prepare_unattended_install": prepare,
        "cleanup_unattended_media": SimpleNamespace(status=cleanup_status, artifacts={}),
        "power_on": SimpleNamespace(status=power_on_status, artifacts={}),
        "clone_vm": SimpleNamespace(status=StepStatus.SUCCEEDED, artifacts={"vm_id": "vm-9"}),
    }
    return SimpleNamespace(
        request=request_for("blank", iso="iso-corp-windows-2025"),
        vmware=SimpleNamespace(remove_temporary_floppy=AsyncMock()),
        target=object(),
        vm_ref=None,
        vm_name="SERVER-PROD-042",
        steps_by_key=steps,
        job_id="job-1",
        db=object(),
        actor_username="operator",
        job=SimpleNamespace(datacenter_name="DC01"),
    )


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.audit import recorder

    monkeypatch.setattr(recorder, "record_audit", lambda _db: SimpleNamespace(record=AsyncMock()))


@pytest.mark.asyncio
async def test_failure_before_boot_removes_media_and_regenerates_it_on_retry() -> None:
    ctx = media_context(StepStatus.FAILED)

    assert await release_unattended_media(ctx, reason="stage 'power_on' failed") is True

    ctx.vmware.remove_temporary_floppy.assert_awaited_once()
    prepare = ctx.steps_by_key["prepare_unattended_install"]
    assert prepare.artifacts["media_removed"] is True
    assert prepare.status == StepStatus.PENDING


@pytest.mark.asyncio
async def test_failure_after_boot_removes_media_without_rerunning_preparation() -> None:
    ctx = media_context(StepStatus.SUCCEEDED)

    assert await release_unattended_media(ctx, reason="job cancelled") is True
    assert ctx.steps_by_key["prepare_unattended_install"].status == StepStatus.SUCCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"cleanup_status": StepStatus.SUCCEEDED}, {"removed": True}])
async def test_already_cleaned_media_is_left_alone(kwargs) -> None:
    ctx = media_context(StepStatus.SUCCEEDED, **kwargs)

    assert await release_unattended_media(ctx, reason="job cancelled") is False
    ctx.vmware.remove_temporary_floppy.assert_not_awaited()


def test_cancel_routing_detects_media_that_may_still_be_attached() -> None:
    from app.repositories.jobs import JobRepository

    def job(prepare_artifacts, cleanup_status=StepStatus.PENDING):
        return SimpleNamespace(
            steps=[
                SimpleNamespace(stage_key="prepare_unattended_install", artifacts=prepare_artifacts),
                SimpleNamespace(stage_key="cleanup_unattended_media", status=cleanup_status, artifacts={}),
            ]
        )

    assert JobRepository.may_hold_unattended_media(job({"datastore_path": "[ds] x.flp"}))
    assert not JobRepository.may_hold_unattended_media(job({}))
    assert not JobRepository.may_hold_unattended_media(job({"datastore_path": "[ds] x.flp", "media_removed": True}))
    assert not JobRepository.may_hold_unattended_media(
        job({"datastore_path": "[ds] x.flp"}, cleanup_status=StepStatus.SUCCEEDED)
    )
