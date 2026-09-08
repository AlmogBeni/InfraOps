"""Prerequisite-aware VM lifecycle regression tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.jobs import (
    GuestOsStatus,
    GuestProvisioningStatus,
    InfrastructureStatus,
    StepStatus,
    VMwareToolsStatus,
)
from app.schemas.provisioning import ProvisioningRequest
from app.services.vmware.base import PowerStateInfo, VmRef
from app.workers.stages import (
    stage_clone_vm,
    stage_prepare_unattended_install,
    stage_wait_for_guest_os,
    stage_wait_for_tools,
)
from tests.conftest import make_request


def request_for(source: str, *, iso: str | None = None) -> ProvisioningRequest:
    payload = make_request().model_dump(mode="json")
    payload["source_type"] = source
    if source == "blank":
        payload["guest"].update(
            template_id=None,
            iso_id=iso,
            hostname="SERVER-PROD-042" if iso else None,
            timezone=None,
            domain_join=None,
        )
        if iso is None:
            payload["network"].update(mode="DHCP", ipv4=None)
    return ProvisioningRequest.model_validate(payload)


def context(request: ProvisioningRequest, vmware) -> SimpleNamespace:
    return SimpleNamespace(
        request=request,
        vmware=vmware,
        target=object(),
        vm_name=request.vm.name,
        vm_ref=VmRef(id="vm-101", name=request.vm.name),
        steps_by_key={"wait_for_guest_os": SimpleNamespace(artifacts={})},
        job=SimpleNamespace(
            infrastructure_status=InfrastructureStatus.READY.value,
            guest_os_status=GuestOsStatus.UNKNOWN.value,
            vmware_tools_status=VMwareToolsStatus.UNKNOWN.value,
            guest_provisioning_status=GuestProvisioningStatus.PENDING.value,
        ),
    )


@pytest.mark.asyncio
async def test_blank_without_iso_waits_for_os_and_never_attempts_tools() -> None:
    vmware = SimpleNamespace(wait_for_tools=AsyncMock())
    ctx = context(request_for("blank"), vmware)

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "WAITING_FOR_PREREQUISITE"
    assert outcome.artifacts["required_action"] == "INSTALL_AND_CONFIRM_GUEST_OS"
    assert ctx.job.guest_os_status == GuestOsStatus.INSTALLATION_REQUIRED.value
    assert ctx.job.vmware_tools_status == VMwareToolsStatus.NOT_APPLICABLE_YET.value
    vmware.wait_for_tools.assert_not_awaited()


@pytest.mark.asyncio
async def test_blank_iso_uses_in_guest_tools_bootstrap_only_during_os_wait() -> None:
    vmware = SimpleNamespace(wait_for_tools=AsyncMock())
    ctx = context(request_for("blank", iso="iso-corp-windows-2025"), vmware)

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    vmware.wait_for_tools.assert_awaited_once()
    assert vmware.wait_for_tools.await_args.kwargs["mount_if_missing"] is True
    assert ctx.job.guest_os_status == GuestOsStatus.READY.value
    assert ctx.job.vmware_tools_status == VMwareToolsStatus.RUNNING.value


@pytest.mark.asyncio
async def test_template_never_receives_blank_unattended_media() -> None:
    vmware = SimpleNamespace(attach_temporary_iso=AsyncMock())
    ctx = context(request_for("template"), vmware)

    outcome = await stage_prepare_unattended_install(ctx)

    assert outcome.status == "NOT_APPLICABLE"
    vmware.attach_temporary_iso.assert_not_awaited()


@pytest.mark.asyncio
async def test_template_wait_observes_existing_tools_without_mounting() -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(),
        get_vm_info=AsyncMock(
            return_value=PowerStateInfo(
                power_state="poweredOn",
                tools_status="toolsOk",
                guest_family="windowsGuest",
            )
        ),
    )
    ctx = context(request_for("template"), vmware)

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert vmware.wait_for_tools.await_args.kwargs["mount_if_missing"] is False
    assert ctx.job.guest_os_status == GuestOsStatus.READY.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("toolsNotInstalled", VMwareToolsStatus.NOT_INSTALLED.value),
        ("toolsNotRunning", VMwareToolsStatus.NOT_RUNNING.value),
    ],
)
async def test_unhealthy_tools_waits_instead_of_reinstalling(
    raw_status: str, expected: str
) -> None:
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(
            return_value=PowerStateInfo(power_state="poweredOn", tools_status=raw_status)
        )
    )
    ctx = context(request_for("template"), vmware)

    outcome = await stage_wait_for_tools(ctx)

    assert outcome.status == "WAITING_FOR_PREREQUISITE"
    assert ctx.job.vmware_tools_status == expected


@pytest.mark.asyncio
async def test_outdated_running_tools_continues_with_warning() -> None:
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(
            return_value=PowerStateInfo(power_state="poweredOn", tools_status="toolsOld")
        )
    )
    ctx = context(request_for("template"), vmware)

    outcome = await stage_wait_for_tools(ctx)

    assert outcome.status == "WARNING"
    assert ctx.job.vmware_tools_status == VMwareToolsStatus.OUTDATED.value


@pytest.mark.asyncio
async def test_modern_tools_fields_take_precedence_over_deprecated_status() -> None:
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(
            return_value=PowerStateInfo(
                power_state="poweredOn",
                tools_status=None,
                tools_running_status="guestToolsRunning",
                tools_version_status="guestToolsSupportedOld",
                guest_operations_ready=True,
            )
        )
    )
    ctx = context(request_for("template"), vmware)

    outcome = await stage_wait_for_tools(ctx)

    assert outcome.status == "WARNING"
    assert ctx.job.vmware_tools_status == VMwareToolsStatus.OUTDATED.value


@pytest.mark.asyncio
async def test_retry_reuses_existing_vm_instead_of_creating_a_duplicate() -> None:
    vmware = SimpleNamespace(
        resolve_vm_id=AsyncMock(return_value="vm-existing"),
        clone_from_template=AsyncMock(),
        create_blank_vm=AsyncMock(),
    )
    ctx = context(request_for("template"), vmware)

    outcome = await stage_clone_vm(ctx)

    assert outcome.status == "SKIPPED"
    assert outcome.artifacts["vm_id"] == "vm-existing"
    vmware.clone_from_template.assert_not_awaited()
    vmware.create_blank_vm.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_stops_after_clone_failure_before_guest_operations() -> None:
    from app.workers.pipeline import ProvisioningPipeline

    clone = SimpleNamespace(stage_key="clone_vm", status=StepStatus.PENDING)
    tools = SimpleNamespace(stage_key="wait_for_tools", status=StepStatus.PENDING)
    job = SimpleNamespace(cancel_requested=False, steps=[clone, tools])
    db = SimpleNamespace(refresh=AsyncMock())
    ctx = SimpleNamespace(
        db=db,
        job=job,
        steps_by_key={"clone_vm": clone, "wait_for_tools": tools},
    )

    class RecordingPipeline(ProvisioningPipeline):
        def __init__(self) -> None:
            self.executed: list[str] = []

        async def _run_stage(self, _ctx, stage, step) -> None:
            self.executed.append(stage.key)
            if stage.key == "clone_vm":
                step.status = StepStatus.FAILED

        async def _finalize_success(self, _ctx) -> None:  # pragma: no cover
            raise AssertionError("a failed clone must not finalize")

    pipeline = RecordingPipeline()
    await pipeline.execute(ctx)

    assert pipeline.executed == ["clone_vm"]
    assert tools.status == StepStatus.PENDING
