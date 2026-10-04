"""Unattended ISO installation lifecycle regression tests."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import InfraOperationError
from app.models.jobs import (
    GuestOsStatus,
    GuestProvisioningStatus,
    InfrastructureStatus,
    JobStatus,
    StepStatus,
    VMwareToolsStatus,
)
from app.schemas.provisioning import DiskProvisioning, DiskSpec, ProvisioningRequest
from app.services.guest.base import CommandResult, GuestCredentialsRejected
from app.services.vmware.base import PowerStateInfo, VmOwnership, VmRef
from app.workers.stages import (
    DATA_DISKS_NOT_VISIBLE_EXIT,
    stage_add_data_disks,
    stage_cleanup_unattended_media,
    stage_configure_hardware,
    stage_create_vm,
    stage_initialize_data_disks,
    stage_power_on,
    stage_wait_for_guest_os,
    stage_wait_for_tools,
)
from tests.conftest import make_request

JOB_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def with_disks(*sizes: int) -> ProvisioningRequest:
    request = make_request()
    request.hardware.disks = [DiskSpec(size_gb=size, provisioning=DiskProvisioning.THIN) for size in sizes]
    return request


def context(vmware, request: ProvisioningRequest | None = None) -> SimpleNamespace:
    request = request or make_request()
    return SimpleNamespace(
        request=request,
        vmware=vmware,
        target=object(),
        vm_name=request.vm.name,
        vm_ref=VmRef(id="vm-101", name=request.vm.name),
        steps_by_key={"wait_for_guest_os": SimpleNamespace(artifacts={})},
        job_id=JOB_ID,
        db=None,  # effective_timeout_seconds falls back to the defaults
        resolve_guest_credentials=AsyncMock(
            return_value=SimpleNamespace(username="Administrator", password="secret")
        ),
        job=SimpleNamespace(
            infrastructure_status=InfrastructureStatus.READY.value,
            guest_os_status=GuestOsStatus.UNKNOWN.value,
            vmware_tools_status=VMwareToolsStatus.UNKNOWN.value,
            guest_provisioning_status=GuestProvisioningStatus.PENDING.value,
            datacenter_name="DC01",
        ),
    )


def setup_context(vmware, probes: list) -> SimpleNamespace:
    ctx = context(vmware)
    ctx.guest_ops = SimpleNamespace(run_powershell=AsyncMock(side_effect=probes), upload_file=AsyncMock())
    return ctx


def _tools_running(host_name: str | None = None) -> PowerStateInfo:
    return PowerStateInfo(
        power_state="poweredOn",
        tools_status="toolsOk",
        tools_running_status="guestToolsRunning",
        guest_operations_ready=True,
        guest_family="windowsGuest",
        guest_host_name=host_name,
    )


def _setup_state(*, setup: int = 0, oobe: int = 0, image: str = "IMAGE_STATE_COMPLETE",
                 name: str = "SERVER-PROD-042") -> CommandResult:
    payload = {"ImageState": image, "SystemSetupInProgress": setup, "OOBEInProgress": oobe, "ComputerName": name}
    return CommandResult(exit_code=0, stdout=json.dumps(payload), stderr="", duration_seconds=0.1)


def _rejected() -> GuestCredentialsRejected:
    return GuestCredentialsRejected(
        "The guest operating system rejected the automation credentials.",
        reason="Invalid username or password for the local administrator account.",
        recommended_action="n/a",
    )


@pytest.fixture
def fast_setup_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import stages

    for name in ("_SETUP_POLL_SECONDS", "_SETUP_PROBE_SECONDS", "_SETUP_PROBE_SECONDS_AFTER_REJECTION"):
        monkeypatch.setattr(stages, name, 0.0)
    monkeypatch.setattr(stages, "_DATA_DISK_DETECTION_DELAYS", (0.0, 0.0))


# ── Windows Setup ────────────────────────────────────────────────────────────


async def test_iso_installation_is_detected_without_any_confirmation(fast_setup_polling) -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(), get_vm_info=AsyncMock(return_value=_tools_running("SERVER-PROD-042"))
    )
    ctx = setup_context(vmware, [_setup_state()])

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert "installed unattended from the ISO" in outcome.output
    assert ctx.job.guest_os_status == GuestOsStatus.READY.value
    assert ctx.job.guest_provisioning_status == GuestProvisioningStatus.IN_PROGRESS.value
    # The whole installation is one bounded wait: no prompt, no second Tools mount.
    assert vmware.wait_for_tools.await_args.args[1] == "vm-101"
    assert not vmware.wait_for_tools.await_args.kwargs
    ctx.guest_ops.upload_file.assert_not_awaited()


async def test_installation_that_never_reports_tools_fails_with_console_guidance() -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(
            side_effect=InfraOperationError("Tools never ready", reason="timeout", recommended_action="n/a")
        )
    )
    ctx = setup_context(vmware, [])

    with pytest.raises(InfraOperationError, match="did not finish installing from the ISO") as caught:
        await stage_wait_for_guest_os(ctx)

    assert "console screenshot" in caught.value.recommended_action
    assert "Press any key to boot from CD or DVD" in caught.value.recommended_action
    assert caught.value.retryable is True


async def test_media_that_installs_another_operating_system_fails_clearly() -> None:
    linux = PowerStateInfo(power_state="poweredOn", tools_status="toolsOk", guest_family="linuxGuest")
    vmware = SimpleNamespace(wait_for_tools=AsyncMock(), get_vm_info=AsyncMock(return_value=linux))
    ctx = setup_context(vmware, [])

    with pytest.raises(InfraOperationError, match="not Windows") as caught:
        await stage_wait_for_guest_os(ctx)

    assert caught.value.retryable is False
    ctx.guest_ops.run_powershell.assert_not_awaited()


async def test_tools_heartbeat_during_oobe_is_not_os_readiness(fast_setup_polling) -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(), get_vm_info=AsyncMock(return_value=_tools_running("SERVER-PROD-042"))
    )
    in_oobe = _setup_state(setup=1, oobe=1, image="IMAGE_STATE_SPECIALIZE_RESEAL_TO_OOBE")
    ctx = setup_context(vmware, [_rejected(), in_oobe, _setup_state()])

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert ctx.guest_ops.run_powershell.await_count == 3
    assert outcome.artifacts["windows_setup"]["image_state"] == "IMAGE_STATE_COMPLETE"


async def test_no_guest_sign_in_is_attempted_while_tools_is_down(fast_setup_polling) -> None:
    from app.workers.stages import wait_for_windows_setup

    restarting = PowerStateInfo(power_state="poweredOn", tools_running_status="guestToolsNotRunning")
    ctx = setup_context(SimpleNamespace(get_vm_info=AsyncMock(return_value=restarting)), [])

    with pytest.raises(InfraOperationError, match="did not finish its first-boot setup") as caught:
        await wait_for_windows_setup(ctx, asyncio.get_running_loop().time() + 0.05)

    ctx.guest_ops.run_powershell.assert_not_awaited()
    assert "VMware Tools is not running" in caught.value.reason


async def test_persistent_credential_rejection_stops_the_wait(fast_setup_polling) -> None:
    from app.workers.stages import _SETUP_MAX_REJECTED_LOGINS, wait_for_windows_setup

    vmware = SimpleNamespace(get_vm_info=AsyncMock(return_value=_tools_running("SERVER-PROD-042")))
    ctx = setup_context(vmware, [_rejected() for _ in range(_SETUP_MAX_REJECTED_LOGINS)])

    with pytest.raises(InfraOperationError, match="keeps rejecting"):
        await wait_for_windows_setup(ctx, asyncio.get_running_loop().time() + 60)

    assert ctx.guest_ops.run_powershell.await_count == _SETUP_MAX_REJECTED_LOGINS


def test_windows_setup_state_parsing() -> None:
    from app.workers.stages import parse_windows_setup_state

    assert parse_windows_setup_state(_setup_state().stdout).complete
    assert not parse_windows_setup_state(_setup_state(oobe=1).stdout).complete
    assert not parse_windows_setup_state(_setup_state(image="IMAGE_STATE_SPECIALIZE_RESEAL_TO_OOBE").stdout).complete
    # Releases that do not record ImageState rely on the in-progress flags.
    assert parse_windows_setup_state(_setup_state(image="").stdout).complete
    with pytest.raises(ValueError):
        parse_windows_setup_state("not json")


# ── Boot prompt ──────────────────────────────────────────────────────────────


async def test_iso_boot_prompt_is_answered_with_keystrokes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import stages

    monkeypatch.setattr(stages, "_BOOT_KEY_SECONDS", 0.0)
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(return_value=PowerStateInfo(power_state="poweredOff")),
        power_on=AsyncMock(),
        reset=AsyncMock(),
        send_keystrokes=AsyncMock(return_value=1),
    )
    ctx = context(vmware)

    outcome = await stage_power_on(ctx)

    vmware.power_on.assert_awaited_once()
    vmware.reset.assert_not_awaited()
    assert vmware.send_keystrokes.await_args.args[2] == [0x2C]  # space bar
    assert outcome.artifacts == {"boot_keys_sent": 1}
    assert ctx.job.guest_os_status == GuestOsStatus.INSTALLATION_IN_PROGRESS.value

    # A rerun (after this stage failed) restarts the firmware to get the prompt back.
    vmware.get_vm_info.return_value = PowerStateInfo(power_state="poweredOn")
    await stage_power_on(ctx)
    vmware.reset.assert_awaited_once()


def test_boot_prompt_window_covers_slow_firmware_but_ends_long_before_setup_restarts() -> None:
    from app.workers import stages

    # The prompt shows for ~5 s once the firmware reaches the CD; Setup's
    # first restart (where a key press would boot the ISO again) is minutes later.
    assert 30 <= stages._BOOT_KEY_SECONDS <= 120
    assert stages._BOOT_KEY_INTERVAL_SECONDS <= 2


async def test_refused_keystrokes_explain_the_missing_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import stages

    monkeypatch.setattr(stages, "_BOOT_KEY_SECONDS", 0.0)
    refused = InfraOperationError("vCenter denied it", reason="NoPermission", recommended_action="n/a")
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(return_value=PowerStateInfo(power_state="poweredOff")),
        power_on=AsyncMock(),
        send_keystrokes=AsyncMock(side_effect=refused),
    )

    with pytest.raises(InfraOperationError, match="could not be started from the ISO") as caught:
        await stage_power_on(context(vmware))

    assert "Inject USB HID scan codes" in caught.value.recommended_action
    assert caught.value.retryable is True


# ── VMware Tools ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("toolsNotInstalled", VMwareToolsStatus.NOT_INSTALLED.value),
        ("toolsNotRunning", VMwareToolsStatus.NOT_RUNNING.value),
    ],
)
async def test_unhealthy_tools_fails_instead_of_waiting_for_a_person(raw_status: str, expected: str) -> None:
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(return_value=PowerStateInfo(power_state="poweredOn", tools_status=raw_status))
    )
    ctx = context(vmware)

    with pytest.raises(InfraOperationError, match="stopped reporting") as caught:
        await stage_wait_for_tools(ctx)

    assert caught.value.retryable is True
    assert ctx.job.vmware_tools_status == expected


async def test_outdated_running_tools_continues_with_warning() -> None:
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(return_value=PowerStateInfo(power_state="poweredOn", tools_status="toolsOld"))
    )
    ctx = context(vmware)

    outcome = await stage_wait_for_tools(ctx)

    assert outcome.status == "WARNING"
    assert ctx.job.vmware_tools_status == VMwareToolsStatus.OUTDATED.value


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
    ctx = context(vmware)

    outcome = await stage_wait_for_tools(ctx)

    assert outcome.status == "WARNING"
    assert ctx.job.vmware_tools_status == VMwareToolsStatus.OUTDATED.value


def test_vm_info_reports_tools_and_host_name() -> None:
    from app.services.vmware.vsphere import VsphereVMwareService

    vm = SimpleNamespace(
        guest=SimpleNamespace(
            net=None,
            ipAddress=None,
            toolsStatus="toolsOk",
            toolsRunningStatus="guestToolsRunning",
            toolsVersionStatus2="guestToolsCurrent",
            guestState="running",
            guestOperationsReady=True,
            guestFamily="windowsGuest",
            hostName="TEST3",
        ),
        runtime=SimpleNamespace(powerState="poweredOn", host=None),
    )

    info = VsphereVMwareService._power_state_info(vm)

    assert info.guest_host_name == "TEST3"
    assert info.guest_operations_ready is True
    assert info.tools_running_status == "guestToolsRunning"


# ── VM creation ──────────────────────────────────────────────────────────────


async def test_retry_resumes_only_a_vm_created_by_this_job() -> None:
    vmware = SimpleNamespace(
        find_vm_ownership=AsyncMock(
            return_value=VmOwnership(vm_id="vm-existing", name="SERVER-PROD-042", owner_job_id=str(JOB_ID))
        ),
        create_vm=AsyncMock(),
    )
    ctx = context(vmware)

    outcome = await stage_create_vm(ctx)

    assert outcome.status == "SKIPPED"
    assert outcome.artifacts["vm_id"] == "vm-existing"
    vmware.create_vm.assert_not_awaited()


@pytest.mark.parametrize("owner", [None, "33333333-3333-4333-8333-333333333333"])
async def test_existing_vm_not_created_by_this_job_is_never_adopted(owner) -> None:
    vmware = SimpleNamespace(
        find_vm_ownership=AsyncMock(
            return_value=VmOwnership(vm_id="vm-prod", name="SERVER-PROD-042", owner_job_id=owner)
        ),
        create_vm=AsyncMock(),
    )
    ctx = context(vmware)

    with pytest.raises(InfraOperationError) as raised:
        await stage_create_vm(ctx)

    assert raised.value.retryable is False
    assert "not created by this job" in raised.value.human_message
    vmware.create_vm.assert_not_awaited()


async def test_vm_is_created_with_only_its_os_disk_and_the_ownership_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.audit import recorder as recorder_module

    monkeypatch.setattr(recorder_module, "record_audit", lambda _db: SimpleNamespace(record=AsyncMock()))
    vmware = SimpleNamespace(
        find_vm_ownership=AsyncMock(return_value=None),
        create_vm=AsyncMock(return_value=VmRef(id="vm-new", name="SERVER-PROD-042")),
    )
    ctx = context(vmware, with_disks(80, 200, 50))
    ctx.actor_username = "operator"

    outcome = await stage_create_vm(ctx)

    spec = vmware.create_vm.await_args.args[1]
    assert spec.job_id == str(JOB_ID)
    assert spec.iso_id == "iso-corp-windows-2025"
    # Setup sees exactly one disk, so DiskID 0 is always the OS disk.
    assert spec.os_disk.size_gb == 80
    assert outcome.artifacts["owner_job_id"] == str(JOB_ID)
    assert "2 data disk(s) are added after Windows is installed" in outcome.output


async def test_hardware_stage_sets_only_cpu_and_memory() -> None:
    vmware = SimpleNamespace(configure_hardware=AsyncMock())

    await stage_configure_hardware(context(vmware))

    assert vmware.configure_hardware.await_args.kwargs == {"cpu": 4, "memory_mb": 16384}


async def test_pipeline_stops_after_create_failure_before_guest_operations() -> None:
    from app.workers.pipeline import ProvisioningPipeline

    create = SimpleNamespace(stage_key="create_vm", status=StepStatus.PENDING)
    tools = SimpleNamespace(stage_key="wait_for_tools", status=StepStatus.PENDING)
    job = SimpleNamespace(cancel_requested=False, steps=[create, tools])
    ctx = SimpleNamespace(
        db=SimpleNamespace(refresh=AsyncMock()),
        job=job,
        steps_by_key={"create_vm": create, "wait_for_tools": tools},
    )

    class RecordingPipeline(ProvisioningPipeline):
        def __init__(self) -> None:
            self.executed: list[str] = []

        async def _run_stage(self, _ctx, stage, step) -> None:
            self.executed.append(stage.key)
            if stage.key == "create_vm":
                step.status = StepStatus.FAILED

        async def _finalize_success(self, _ctx) -> None:  # pragma: no cover
            raise AssertionError("a failed creation must not finalize")

    pipeline = RecordingPipeline()
    await pipeline.execute(ctx)

    assert pipeline.executed == ["create_vm"]
    assert tools.status == StepStatus.PENDING


@pytest.mark.parametrize(
    ("stage_key", "screenshot"),
    [("wait_for_guest_os", True), ("power_on", True), ("configure_guest_network", False)],
)
async def test_failed_console_stage_keeps_a_console_screenshot(
    monkeypatch: pytest.MonkeyPatch, stage_key: str, screenshot: bool
) -> None:
    from app.workers import pipeline as pipeline_module
    from app.workers.pipeline import ProvisioningPipeline
    from app.workers.state_machine import STAGES_BY_KEY

    monkeypatch.setattr(pipeline_module, "release_unattended_media", AsyncMock(return_value=False))
    monkeypatch.setattr(pipeline_module, "notify_requester", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_module, "AuditRecorder", lambda _db: SimpleNamespace(record=AsyncMock()))
    create = SimpleNamespace(stage_key="create_vm", status=StepStatus.SUCCEEDED, artifacts={"vm_id": "vm-101"})
    step = SimpleNamespace(stage_key=stage_key, status=StepStatus.RUNNING, attempt=1, artifacts={},
                           error_technical=None, retryable=True)
    vmware = SimpleNamespace(capture_screenshot=AsyncMock(return_value="[ds] SERVER/SERVER-1.png"))
    ctx = context(vmware)
    ctx.steps_by_key = {"create_vm": create, stage_key: step}
    ctx.db = SimpleNamespace(commit=AsyncMock())
    ctx.actor_username = "operator"
    ctx.job = SimpleNamespace(
        steps=[create, step], status=JobStatus.RUNNING, started_at=None, datacenter_name="DC01",
        vm_name="SERVER-PROD-042",
        infrastructure_status="READY", guest_os_status="INSTALLATION_IN_PROGRESS",
        vmware_tools_status="UNKNOWN", guest_provisioning_status="WAITING_FOR_OS",
    )
    failure = InfraOperationError("Windows did not finish", reason="timeout", recommended_action="look",
                                  technical_detail="waited")
    publisher = SimpleNamespace(publish_stage=AsyncMock())

    await ProvisioningPipeline(publisher)._handle_failure(
        ctx, STAGES_BY_KEY[stage_key], step, failure, None, None
    )

    assert ctx.job.status == JobStatus.PARTIALLY_COMPLETED  # the VM exists; nothing waits for a person
    assert step.status == StepStatus.FAILED
    if screenshot:
        assert step.artifacts["console_screenshot"] == "[ds] SERVER/SERVER-1.png"
        assert "Console screenshot at failure: [ds] SERVER/SERVER-1.png" in step.error_technical
    else:
        vmware.capture_screenshot.assert_not_awaited()
        assert "console_screenshot" not in step.artifacts


# ── Media cleanup ────────────────────────────────────────────────────────────


def cleanup_context(vmware, scrub_exit: int = 0) -> SimpleNamespace:
    ctx = context(vmware)
    ctx.steps_by_key = {
        "prepare_unattended_install": SimpleNamespace(
            artifacts={"datastore_path": "[ds] infraops-unattend/infraops-job.iso"}
        )
    }
    ctx.guest_ops = SimpleNamespace(
        run_powershell=AsyncMock(
            return_value=CommandResult(exit_code=scrub_exit, stdout="ANSWER-FILE-SCRUBBED", stderr="",
                                       duration_seconds=0.1)
        )
    )
    return ctx


async def test_cleanup_removes_answer_media_scrubs_the_guest_and_disconnects_the_isos() -> None:
    vmware = SimpleNamespace(
        remove_answer_media=AsyncMock(),
        detach_installation_media=AsyncMock(
            return_value=["[PROD-SAN-01] ISO/Windows Server 2025.iso", "[] /vmimages/tools-isoimages/windows.iso"]
        ),
    )
    ctx = cleanup_context(vmware)

    outcome = await stage_cleanup_unattended_media(ctx)

    assert outcome.status == "SUCCEEDED"
    assert vmware.remove_answer_media.await_args.kwargs["datastore_path"] == "[ds] infraops-unattend/infraops-job.iso"
    assert ctx.steps_by_key["prepare_unattended_install"].artifacts["media_removed"] is True
    script = ctx.guest_ops.run_powershell.await_args.args[3]
    assert "DefaultPassword" in script and "AutoAdminLogon" in script and "Panther" in script
    vmware.detach_installation_media.assert_awaited_once_with(ctx.target, "vm-101")
    assert "boot order set to disk only" in outcome.output


async def test_iso_that_cannot_be_disconnected_is_a_warning_not_a_failure() -> None:
    vmware = SimpleNamespace(
        remove_answer_media=AsyncMock(),
        detach_installation_media=AsyncMock(
            side_effect=InfraOperationError("CD-ROM locked", reason="busy", recommended_action="n/a")
        ),
    )

    outcome = await stage_cleanup_unattended_media(cleanup_context(vmware))

    assert outcome.status == "WARNING"
    assert "could not be disconnected" in outcome.output


# ── Data disks ───────────────────────────────────────────────────────────────


async def test_vm_without_data_disks_skips_both_disk_stages() -> None:
    vmware = SimpleNamespace(add_data_disks=AsyncMock())
    ctx = context(vmware, with_disks(100))
    ctx.guest_ops = SimpleNamespace(run_powershell=AsyncMock())

    assert (await stage_add_data_disks(ctx)).status == "SKIPPED"
    assert (await stage_initialize_data_disks(ctx)).status == "SKIPPED"
    vmware.add_data_disks.assert_not_awaited()
    ctx.guest_ops.run_powershell.assert_not_awaited()


async def test_data_disks_are_hot_added_after_installation() -> None:
    vmware = SimpleNamespace(add_data_disks=AsyncMock(return_value=2))
    ctx = context(vmware, with_disks(100, 200, 50))

    outcome = await stage_add_data_disks(ctx)

    disks = vmware.add_data_disks.await_args.args[2]
    assert [disk.size_gb for disk in disks] == [200, 50]
    assert outcome.artifacts == {"data_disks": [200, 50]}


def _volumes(*sizes: int, file_system: str = "NTFS") -> CommandResult:
    payload = [
        {"Number": index, "SizeGB": size, "DriveLetter": chr(ord("D") + index), "Label": f"Data{index}",
         "FileSystem": file_system}
        for index, size in enumerate(sizes, start=1)
    ]
    return CommandResult(exit_code=0, stdout=json.dumps(payload), stderr="", duration_seconds=0.1)


async def test_data_disks_are_formatted_once_windows_detects_them(fast_setup_polling) -> None:
    not_yet = CommandResult(exit_code=DATA_DISKS_NOT_VISIBLE_EXIT, stdout="FOUND:1", stderr="", duration_seconds=0.1)
    ctx = context(SimpleNamespace(), with_disks(100, 200, 50))
    ctx.guest_ops = SimpleNamespace(run_powershell=AsyncMock(side_effect=[not_yet, _volumes(200, 50)]))

    outcome = await stage_initialize_data_disks(ctx)

    script = ctx.guest_ops.run_powershell.await_args.args[3]
    assert "Initialize-Disk" in script and "IsOffline" in script and "IsReadOnly" in script
    assert "Format-Volume" in script
    assert [volume["size_gb"] for volume in outcome.artifacts["volumes"]] == [200, 50]
    assert "Disk 1: 200 GB NTFS E:" in outcome.output


async def test_disks_windows_never_detects_fail_the_stage(fast_setup_polling) -> None:
    not_yet = CommandResult(exit_code=DATA_DISKS_NOT_VISIBLE_EXIT, stdout="FOUND:0", stderr="", duration_seconds=0.1)
    ctx = context(SimpleNamespace(), with_disks(100, 200))
    ctx.guest_ops = SimpleNamespace(run_powershell=AsyncMock(return_value=not_yet))

    with pytest.raises(InfraOperationError, match="did not detect every hot-added data disk") as caught:
        await stage_initialize_data_disks(ctx)

    assert caught.value.retryable is True


async def test_unexpected_disk_sizes_fail_the_stage(fast_setup_polling) -> None:
    ctx = context(SimpleNamespace(), with_disks(100, 200))
    ctx.guest_ops = SimpleNamespace(run_powershell=AsyncMock(return_value=_volumes(150)))

    with pytest.raises(InfraOperationError, match="do not match the request"):
        await stage_initialize_data_disks(ctx)


# ── Network re-check ─────────────────────────────────────────────────────────


async def test_static_address_taken_after_submission_blocks_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas.provisioning import ConflictProviderStatus, ProviderResult
    from app.services.network import conflict
    from app.workers.stages import assert_static_address_unclaimed

    class Replies(conflict.IcmpPingProvider):
        async def check(self, address, prefix):
            return ProviderResult(provider="ICMP", status=ConflictProviderStatus.CONFLICT_DETECTED,
                                  detail=f"A device responded to ICMP echo at {address}.")

    monkeypatch.setattr(conflict, "IcmpPingProvider", Replies)
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(return_value=PowerStateInfo(power_state="poweredOn", ip_addresses=[])),
        get_used_ips=AsyncMock(return_value={"10.20.30.45": "SERVER-PROD-042"}),
    )
    ctx = context(vmware)

    with pytest.raises(InfraOperationError, match="already in use"):
        await assert_static_address_unclaimed(ctx, "10.20.30.45", 24)

    # The VM's own address (retry) is never reported as a conflict.
    vmware.get_vm_info.return_value = PowerStateInfo(power_state="poweredOn", ip_addresses=["10.20.30.45"])
    await assert_static_address_unclaimed(ctx, "10.20.30.45", 24)


@pytest.mark.parametrize("scrub_exit", [1, None])
async def test_answer_file_left_in_the_guest_stops_the_job(scrub_exit) -> None:
    vmware = SimpleNamespace(remove_answer_media=AsyncMock(), detach_installation_media=AsyncMock())
    ctx = cleanup_context(vmware, scrub_exit=scrub_exit or 0)
    if scrub_exit is None:
        ctx.guest_ops.run_powershell.side_effect = InfraOperationError(
            "Guest operations unavailable", reason="Tools restarting", recommended_action="n/a"
        )

    with pytest.raises(InfraOperationError, match="cached answer file could not be removed") as caught:
        await stage_cleanup_unattended_media(ctx)

    assert caught.value.retryable is True
    vmware.detach_installation_media.assert_not_awaited()
