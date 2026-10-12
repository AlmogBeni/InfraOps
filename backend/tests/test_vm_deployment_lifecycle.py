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
from app.schemas.infrastructure import DatastoreOut
from app.schemas.provisioning import DiskProvisioning, DiskSpec, ProvisioningRequest
from app.services.guest.base import CommandResult
from app.services.vmware.base import PowerStateInfo, VmOwnership, VmQuestionPending, VmRef
from app.workers.stages import (
    DATA_DISKS_NOT_VISIBLE_EXIT,
    GUEST_BOOT_TIME_SCRIPT,
    SHUTDOWN_PATH,
    stage_add_data_disks,
    stage_cleanup_unattended_media,
    stage_configure_hardware,
    stage_create_vm,
    stage_initialize_data_disks,
    stage_power_on,
    stage_reboot_guest,
    stage_wait_for_guest_os,
    stage_wait_for_tools,
    stage_wait_guest_ready,
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


@pytest.fixture
def fast_setup_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import stages

    monkeypatch.setattr(stages, "_DATA_DISK_DETECTION_DELAYS", (0.0, 0.0))


# ── Windows Setup ────────────────────────────────────────────────────────────


def _installing(cpu_mhz: int, power_state: str = "poweredOn") -> PowerStateInfo:
    return PowerStateInfo(power_state=power_state, cpu_usage_mhz=cpu_mhz, cpu_capacity_mhz=4800)


@pytest.fixture
async def fast_install_watch(monkeypatch: pytest.MonkeyPatch):
    """Poll without sleeping and count each poll as a minute of wall time."""
    from app.workers import stages

    clock = SimpleNamespace(now=0.0)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "time", lambda: clock.now)

    async def no_sleep(_seconds):
        clock.now += 60.0

    monkeypatch.setattr(stages.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(stages, "effective_timeout_seconds", AsyncMock(return_value=6 * 3600))
    return clock


def watch_context(infos: list[PowerStateInfo]) -> SimpleNamespace:
    vmware = SimpleNamespace(get_vm_info_by_id=AsyncMock(side_effect=infos))
    ctx = setup_context(vmware, [])
    step = SimpleNamespace(stage_key="wait_for_guest_os", artifacts={}, output=None)
    ctx.job.steps = [step]
    ctx.job.progress = 30
    ctx.steps_by_key = {"wait_for_guest_os": step}
    ctx.db = SimpleNamespace(commit=AsyncMock())
    ctx.publisher = SimpleNamespace(publish_stage=AsyncMock())
    return ctx


async def test_running_tools_from_first_logon_proves_windows_is_installed(fast_install_watch) -> None:
    ctx = watch_context([_tools_running("TEST123")])

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert "installed unattended from the ISO" in outcome.output
    assert outcome.artifacts == {"windows_setup": {"computer_name": "TEST123"}}
    assert ctx.job.guest_os_status == GuestOsStatus.READY.value
    assert ctx.job.guest_provisioning_status == GuestProvisioningStatus.IN_PROGRESS.value
    assert ctx.vmware.get_vm_info_by_id.await_args.args[1] == "vm-101"
    # No guest file transfer, so ESXi connectivity cannot make this stage fail.
    ctx.guest_ops.run_powershell.assert_not_awaited()
    ctx.guest_ops.upload_file.assert_not_awaited()


async def test_a_slow_but_busy_installation_is_watched_and_reported_live(fast_install_watch) -> None:
    # Three hours of busy Setup, then Tools: far beyond the old fixed two hours.
    ctx = watch_context([_installing(2100)] * 180 + [_tools_running("TEST123")])

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert "in 3 h 00 min" in outcome.output
    published = ctx.publisher.publish_stage.await_args_list
    assert len(published) == 180  # once a minute while Setup runs
    first, last = published[0].kwargs["message"], published[-1].kwargs["message"]
    assert first.startswith("Windows Setup running for 0 min: VM busy (CPU 2.1 GHz)")
    assert last.startswith("Windows Setup running for 2 h 59 min: VM busy")
    assert ctx.db.commit.await_count == 180
    assert "live_status" not in ctx.steps_by_key["wait_for_guest_os"].artifacts


async def test_an_idle_vm_without_tools_fails_as_stalled(fast_install_watch) -> None:
    # Busy for 10 minutes, then idle: Setup is waiting on a page or an error.
    ctx = watch_context([_installing(2000)] * 10 + [_installing(40)] * 60)

    with pytest.raises(InfraOperationError, match="stopped making progress") as caught:
        await stage_wait_for_guest_os(ctx)

    assert "idle for 30 min" in caught.value.reason
    assert "console screenshot" in caught.value.recommended_action
    assert "infraops-firstlogon.log" in caught.value.recommended_action
    assert caught.value.retryable is True
    assert ctx.steps_by_key["wait_for_guest_os"].artifacts["live_status"]["text"].startswith(
        "Windows Setup running for 39 min: VM idle for 30 min"
    )


async def test_the_absolute_limit_still_applies_to_an_endlessly_busy_vm(fast_install_watch) -> None:
    from app.workers import stages

    stages.effective_timeout_seconds.return_value = 30 * 60
    ctx = watch_context([_installing(2000)] * 60)

    with pytest.raises(InfraOperationError, match="did not finish within the 30 min limit") as caught:
        await stage_wait_for_guest_os(ctx)

    assert "still active" in caught.value.reason
    assert "Settings" in caught.value.recommended_action


async def test_a_vm_powered_off_during_installation_fails_immediately(fast_install_watch) -> None:
    ctx = watch_context([_installing(2000), _installing(0, power_state="poweredOff")])

    with pytest.raises(InfraOperationError, match="powered off while Windows was installing"):
        await stage_wait_for_guest_os(ctx)


async def test_media_that_installs_another_operating_system_fails_clearly(fast_install_watch) -> None:
    linux = PowerStateInfo(
        power_state="poweredOn", tools_status="toolsOk", guest_operations_ready=True, guest_family="linuxGuest"
    )
    ctx = watch_context([linux])

    with pytest.raises(InfraOperationError, match="not Windows") as caught:
        await stage_wait_for_guest_os(ctx)

    assert caught.value.retryable is False
    ctx.guest_ops.run_powershell.assert_not_awaited()


def test_vm_activity_comes_from_the_host_not_vmware_tools() -> None:
    from app.services.vmware.vsphere import VsphereVMwareService

    vm = SimpleNamespace(
        guest=SimpleNamespace(net=None, ipAddress=None),
        runtime=SimpleNamespace(powerState="poweredOn", host=None, maxCpuUsage=4800),
        summary=SimpleNamespace(quickStats=SimpleNamespace(overallCpuUsage=2150)),
    )

    info = VsphereVMwareService._power_state_info(vm)

    assert (info.cpu_usage_mhz, info.cpu_capacity_mhz) == (2150, 4800)
    assert info.tools_status is None


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

    # The prompt shows for ~5 s once the firmware reaches the CD and returns with
    # the 10 s boot retry; Setup's first restart (where a key press would boot the
    # ISO again) is minutes later.
    assert 30 <= stages._BOOT_KEY_SECONDS <= 120
    assert stages._BOOT_KEY_INTERVAL_SECONDS < 5
    # Each press is a vCenter task; keep the Recent Tasks list readable.
    assert stages._BOOT_KEY_SECONDS / stages._BOOT_KEY_INTERVAL_SECONDS <= 20


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


def _datastore(id_: str, name: str, free_gb: float, accessible: bool = True) -> DatastoreOut:
    return DatastoreOut(id=id_, name=name, type="VMFS", capacity_gb=4000, free_gb=free_gb,
                        usage_percent=50, accessible=accessible)


DATASTORES = [
    _datastore("datastore-slow", "CAPACITY-NFS", 9000),  # most free space, slow tier
    _datastore("datastore-fast", "ALLFLASH-01", 1200),
    _datastore("datastore-full", "ALLFLASH-02", 100),
]


def create_context(monkeypatch: pytest.MonkeyPatch, request: ProvisioningRequest, preferred: list[str]):
    from app.audit import recorder as recorder_module
    from app.workers import stages

    monkeypatch.setattr(recorder_module, "record_audit", lambda _db: SimpleNamespace(record=AsyncMock()))
    monkeypatch.setattr(stages, "load_effective", AsyncMock(return_value={"preferred_datastores": preferred}))
    vmware = SimpleNamespace(
        find_vm_ownership=AsyncMock(return_value=None),
        create_vm=AsyncMock(return_value=VmRef(id="vm-new", name="SERVER-PROD-042")),
        get_datastores=AsyncMock(return_value=DATASTORES),
    )
    ctx = context(vmware, request)
    ctx.actor_username = "operator"
    return ctx


async def test_vm_is_created_with_only_its_os_disk_and_the_ownership_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = create_context(monkeypatch, with_disks(80, 200, 50), preferred=["ALLFLASH-01"])

    outcome = await stage_create_vm(ctx)

    spec = ctx.vmware.create_vm.await_args.args[1]
    assert spec.job_id == str(JOB_ID)
    assert spec.iso_id == "iso-corp-windows-2025"
    # Setup sees exactly one disk, so DiskID 0 is always the OS disk.
    assert spec.os_disk.size_gb == 80
    assert outcome.artifacts["owner_job_id"] == str(JOB_ID)
    assert "2 data disk(s) are added on the same datastore after Windows is installed" in outcome.output


async def test_automatic_storage_uses_the_first_preferred_datastore_with_room(monkeypatch) -> None:
    # ALLFLASH-02 is first but too small for 330 GB; the slow tier is never used.
    ctx = create_context(monkeypatch, with_disks(80, 200, 50), preferred=["allflash-02", "ALLFLASH-01"])

    outcome = await stage_create_vm(ctx)

    assert ctx.vmware.create_vm.await_args.args[1].datastore_id == "datastore-fast"
    assert outcome.artifacts["datastore_id"] == "datastore-fast"
    assert "Datastore: ALLFLASH-01 (preferred datastore 2 of 2, 1200 GB free)." in outcome.output


async def test_without_preferred_datastores_the_most_free_space_still_decides(monkeypatch) -> None:
    ctx = create_context(monkeypatch, with_disks(100), preferred=[])

    outcome = await stage_create_vm(ctx)

    assert ctx.vmware.create_vm.await_args.args[1].datastore_id == "datastore-slow"
    assert "no preferred datastores are configured" in outcome.output


async def test_no_preferred_datastore_with_room_stops_before_creating_anything(monkeypatch) -> None:
    ctx = create_context(monkeypatch, with_disks(2000), preferred=["ALLFLASH-01", "ALLFLASH-02", "GONE"])

    with pytest.raises(InfraOperationError, match="No datastore can take this VM") as caught:
        await stage_create_vm(ctx)

    assert "ALLFLASH-01 has 1200 GB free" in caught.value.reason
    assert "GONE is not connected to this compute target" in caught.value.reason
    ctx.vmware.create_vm.assert_not_awaited()


async def test_a_datastore_chosen_in_the_wizard_wins(monkeypatch) -> None:
    request = with_disks(100)
    request.hardware.disks[0].datastore_id = "datastore-full"
    ctx = create_context(monkeypatch, request, preferred=["ALLFLASH-01"])

    outcome = await stage_create_vm(ctx)

    assert ctx.vmware.create_vm.await_args.args[1].datastore_id == "datastore-full"
    assert "ALLFLASH-02 (selected in the request)" in outcome.output


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


class RestartingWindows:
    """Guest double that runs scripts and really restarts on shutdown.exe."""

    def __init__(self, scrub_exit: int = 0, *, restarts: bool = True, probes_while_down: int = 2) -> None:
        self.scrub_exit = scrub_exit
        self.restarts = restarts
        self.probes_while_down = probes_while_down
        self.boot_time = "2026-10-12T08:00:00.0000000Z"
        self.down = 0
        self.scripts: list[str] = []
        self.programs: list[tuple[str, str]] = []
        self.scrub_error: Exception | None = None

    async def run_powershell(self, target, vm_name, credentials, script, timeout_seconds, **kwargs):
        self.scripts.append(script)
        if script == GUEST_BOOT_TIME_SCRIPT:
            if self.down:
                self.down -= 1
                raise InfraOperationError(
                    "VMware Tools guest operations are unavailable inside the VM.",
                    reason="Windows is restarting.",
                    recommended_action="n/a",
                )
            return CommandResult(exit_code=0, stdout=f"{self.boot_time}\r\n", stderr="", duration_seconds=0.1)
        if self.scrub_error is not None:
            raise self.scrub_error
        return CommandResult(exit_code=self.scrub_exit, stdout="ANSWER-FILE-SCRUBBED", stderr="",
                             duration_seconds=0.1)

    async def run_program(self, target, vm_name, credentials, program_path, arguments, timeout_seconds,
                          working_directory=None):
        self.programs.append((program_path, arguments))
        if self.restarts:
            self.down = self.probes_while_down
            self.boot_time = "2026-10-12T08:04:00.0000000Z"
        return CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.1)


@pytest.fixture
def fast_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import stages

    monkeypatch.setattr(stages, "_RESTART_POLL_SECONDS", 0.0)


def cleanup_context(vmware, scrub_exit: int = 0) -> SimpleNamespace:
    ctx = context(vmware)
    ctx.steps_by_key = {
        "prepare_unattended_install": SimpleNamespace(
            artifacts={"datastore_path": "[ds] infraops-unattend/infraops-job.iso"}
        )
    }
    ctx.guest_ops = RestartingWindows(scrub_exit)
    return ctx


async def test_cleanup_removes_answer_media_scrubs_the_guest_and_disconnects_the_isos(fast_restart) -> None:
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
    script = ctx.guest_ops.scripts[0]
    assert "DefaultPassword" in script and "AutoAdminLogon" in script and "Panther" in script
    vmware.detach_installation_media.assert_awaited_once_with(ctx.target, "vm-101")
    assert "boot order set to disk only" in outcome.output


async def test_cleanup_restarts_windows_so_the_tools_drivers_load(fast_restart) -> None:
    vmware = SimpleNamespace(remove_answer_media=AsyncMock(), detach_installation_media=AsyncMock(return_value=[]))
    ctx = cleanup_context(vmware)

    outcome = await stage_cleanup_unattended_media(ctx)

    [(program, arguments)] = ctx.guest_ops.programs
    assert program == SHUTDOWN_PATH and arguments.startswith("/r /t 10")
    # The restart is confirmed by a new boot time, not by Tools answering
    # while Windows is still going down.
    assert ctx.guest_ops.scripts.count(GUEST_BOOT_TIME_SCRIPT) == 4  # before, 2 while down, after
    assert "Windows restarted from its disk to load the VMware Tools drivers" in outcome.output


async def test_windows_that_never_restarts_fails_cleanup(fast_restart, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import stages

    monkeypatch.setattr(stages, "_TOOLS_RESTART_SECONDS", 0.0)
    vmware = SimpleNamespace(remove_answer_media=AsyncMock(), detach_installation_media=AsyncMock(return_value=[]))
    ctx = cleanup_context(vmware)
    ctx.guest_ops.restarts = False

    with pytest.raises(InfraOperationError, match="did not come back after the restart") as caught:
        await stage_cleanup_unattended_media(ctx)

    assert caught.value.retryable is True
    assert "Windows had not restarted yet" in caught.value.reason


async def test_vm_frozen_by_a_vcenter_question_stops_cleanup_with_the_question() -> None:
    vmware = SimpleNamespace(
        remove_answer_media=AsyncMock(),
        detach_installation_media=AsyncMock(
            side_effect=VmQuestionPending(
                "The VM is waiting for an answer to a question in vCenter.",
                reason="vCenter asked: The guest operating system has locked the CD-ROM door.",
                recommended_action="Answer the question in the vSphere Client, then retry this stage.",
            )
        ),
    )
    ctx = cleanup_context(vmware)

    with pytest.raises(VmQuestionPending, match="waiting for an answer"):
        await stage_cleanup_unattended_media(ctx)

    assert ctx.guest_ops.scripts == []  # nothing is attempted inside a frozen VM


async def test_iso_that_cannot_be_disconnected_is_a_warning_not_a_failure(fast_restart) -> None:
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
async def test_answer_file_left_in_the_guest_stops_the_job_after_the_media_is_released(scrub_exit) -> None:
    from app.services.guest.base import GuestFileTransferError

    vmware = SimpleNamespace(remove_answer_media=AsyncMock(), detach_installation_media=AsyncMock(return_value=[]))
    ctx = cleanup_context(vmware, scrub_exit=scrub_exit or 0)
    if scrub_exit is None:
        ctx.guest_ops.scrub_error = GuestFileTransferError(
            "InfraOps could not connect to ESXi host 'esx01.corp' on port 443.",
            reason="Connection refused",
            recommended_action="Allow HTTPS (TCP 443) from the InfraOps server to every ESXi host.",
        )

    with pytest.raises(InfraOperationError, match="cached answer file could not be removed") as caught:
        await stage_cleanup_unattended_media(ctx)

    assert caught.value.retryable is True
    # The answer CD and the installation ISOs are released before the guest step.
    vmware.remove_answer_media.assert_awaited_once()
    vmware.detach_installation_media.assert_awaited_once()
    if scrub_exit is None:
        assert "esx01.corp" in caught.value.reason
        assert caught.value.recommended_action.startswith("Allow HTTPS (TCP 443)")


# ── Restart after the domain join ────────────────────────────────────────────


def restart_context() -> SimpleNamespace:
    ctx = context(SimpleNamespace())
    ctx.hostname_changed = True
    ctx.steps_by_key = {"join_domain": SimpleNamespace(artifacts={"joined": True})}
    ctx.guest_ops = RestartingWindows()
    return ctx


async def test_guest_ready_waits_for_the_restart_instead_of_a_fixed_delay(fast_restart) -> None:
    ctx = restart_context()

    reboot = await stage_reboot_guest(ctx)
    assert reboot.artifacts == {"boot_time": "2026-10-12T08:00:00.0000000Z"}
    ctx.steps_by_key["reboot_guest"] = SimpleNamespace(status=StepStatus.SUCCEEDED, artifacts=reboot.artifacts)

    ready = await stage_wait_guest_ready(ctx)

    assert ready.output == "Guest responsive after restart (3 probe(s))."


async def test_mock_guest_reports_a_new_boot_time_after_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.guest import mock as guest_mock

    guest_mock.reset_mock_guests()
    monkeypatch.setattr(guest_mock, "_LATENCY_PROGRAM", 0)
    guest = guest_mock.MockGuestOperations()
    credentials = SimpleNamespace(username="Administrator", password="secret")

    async def boot_time() -> str:
        return (await guest.run_powershell(object(), "SRV-RESTART", credentials, GUEST_BOOT_TIME_SCRIPT, 60)).stdout

    before = await boot_time()
    assert await boot_time() == before
    await guest.run_program(object(), "SRV-RESTART", credentials, SHUTDOWN_PATH, "/r /t 10", 60)
    assert await boot_time() > before
