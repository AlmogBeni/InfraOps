"""Timeouts and cancellation must stop the underlying vCenter/guest work."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.jobs import StepStatus
from app.services.vmware import vsphere
from app.services.vmware.vsphere import VCenterTaskCancelled, VsphereVMwareService


class FakeTask:
    def __init__(self) -> None:
        self._moId = "task-1"
        self.info = SimpleNamespace(state=vsphere.vim.TaskInfo.State.running, error=None)
        self.cancelled = threading.Event()

    def CancelTask(self) -> None:  # noqa: N802 - pyvmomi naming
        self.cancelled.set()
        self.info.state = vsphere.vim.TaskInfo.State.error


def test_wait_for_task_cancels_vcenter_task_when_caller_is_cancelled() -> None:
    task = FakeTask()
    event = threading.Event()
    token = vsphere._CANCEL_EVENT.set(event)
    try:
        event.set()
        with pytest.raises(VCenterTaskCancelled):
            VsphereVMwareService._wait_for_task(task)
    finally:
        vsphere._CANCEL_EVENT.reset(token)
    assert task.cancelled.is_set()


@pytest.mark.asyncio
async def test_stage_timeout_propagates_to_the_blocking_task_poller(monkeypatch: pytest.MonkeyPatch) -> None:
    task = FakeTask()
    service = object.__new__(VsphereVMwareService)
    service._cache = SimpleNamespace(evict=lambda _id: None)
    monkeypatch.setattr(service, "_get_session", AsyncMock(return_value=object()))
    monkeypatch.setattr(vsphere, "_TASK_POLL_INTERVAL", 0.01)

    def op(_si):
        VsphereVMwareService._wait_for_task(task)

    target = SimpleNamespace(id="vc", host="vc.example.test")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(service._with_session(target, op), timeout=0.1)
    deadline = time.monotonic() + 2
    while not task.cancelled.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert task.cancelled.is_set(), "the vCenter task must be cancelled after the stage timeout"


@pytest.mark.asyncio
async def test_pipeline_cancels_a_running_stage_on_user_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import pipeline as pipeline_module
    from app.workers.pipeline import ProvisioningPipeline
    from app.workers.state_machine import STAGES_BY_KEY

    handler_cancelled = asyncio.Event()

    async def slow_handler(_ctx):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            handler_cancelled.set()
            raise

    monkeypatch.setitem(pipeline_module.STAGE_HANDLERS, "clone_vm", slow_handler)
    monkeypatch.setattr(pipeline_module, "effective_timeout_seconds", AsyncMock(return_value=60))

    step = SimpleNamespace(stage_key="clone_vm", status=StepStatus.PENDING, attempt=0)
    job = SimpleNamespace(steps=[step], current_stage=None, progress=0)
    ctx = SimpleNamespace(
        job=job,
        job_id="job-1",
        db=SimpleNamespace(commit=AsyncMock()),
        cancel_event=asyncio.Event(),
    )

    pipeline = ProvisioningPipeline(publisher=SimpleNamespace(publish_stage=AsyncMock()))
    applied: list[str] = []

    async def apply_cancellation(_ctx):
        applied.append("cancelled")

    monkeypatch.setattr(pipeline, "_apply_cancellation", apply_cancellation)
    monkeypatch.setattr(pipeline_module.JobRepository, "compute_progress", staticmethod(lambda _job: 0))

    run = asyncio.create_task(pipeline._run_stage(ctx, STAGES_BY_KEY["clone_vm"], step))
    await asyncio.sleep(0.05)
    ctx.cancel_event.set()
    await asyncio.wait_for(run, timeout=2)

    assert handler_cancelled.is_set()
    assert step.status == StepStatus.CANCELLED
    assert applied == ["cancelled"]
