"""Prerequisite-aware VM lifecycle regression tests."""

from __future__ import annotations

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
    StepStatus,
    VMwareToolsStatus,
)
from app.schemas.provisioning import ProvisioningRequest
from app.services.guest.base import CommandResult, GuestCredentialsRejected
from app.services.vmware.base import PowerStateInfo, TemporaryMediaRef, VmOwnership, VmRef
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
async def test_blank_iso_waits_for_confirmation_without_mounting_tools() -> None:
    vmware = SimpleNamespace(wait_for_tools=AsyncMock())
    ctx = context(request_for("blank", iso="iso-corp-windows-2025"), vmware)

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "WAITING_FOR_PREREQUISITE"
    assert outcome.artifacts["required_action"] == "CONFIRM_UNATTENDED_OS_INSTALLATION"
    vmware.wait_for_tools.assert_not_awaited()
    assert ctx.job.guest_os_status == GuestOsStatus.INSTALLATION_IN_PROGRESS.value
    assert ctx.job.vmware_tools_status == VMwareToolsStatus.NOT_APPLICABLE_YET.value


@pytest.mark.asyncio
async def test_blank_iso_mounts_tools_only_after_os_confirmation() -> None:
    vmware = SimpleNamespace(wait_for_tools=AsyncMock())
    ctx = context(request_for("blank", iso="iso-corp-windows-2025"), vmware)
    ctx.steps_by_key["wait_for_guest_os"].artifacts = {"administrator_confirmed": True}

    os_outcome = await stage_wait_for_guest_os(ctx)

    assert os_outcome.status == "SUCCEEDED"
    assert ctx.job.guest_os_status == GuestOsStatus.READY.value
    vmware.wait_for_tools.assert_not_awaited()

    ctx.vmware = SimpleNamespace(
        get_vm_info=AsyncMock(
            return_value=PowerStateInfo(
                power_state="poweredOn",
                tools_status="toolsNotInstalled",
                guest_family="windowsGuest",
            )
        ),
        mount_tools_installer=AsyncMock(return_value=True),
        wait_for_tools=AsyncMock(),
    )
    tools_outcome = await stage_wait_for_tools(ctx)

    assert tools_outcome.status == "SUCCEEDED"
    ctx.vmware.mount_tools_installer.assert_awaited_once()
    assert ctx.vmware.wait_for_tools.await_args.kwargs["mount_if_missing"] is False
    assert ctx.job.vmware_tools_status == VMwareToolsStatus.RUNNING.value


@pytest.mark.asyncio
async def test_blank_iso_reports_busy_cdrom_before_tools_installation() -> None:
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(
            return_value=PowerStateInfo(
                power_state="poweredOn",
                tools_status="toolsNotInstalled",
                guest_family="windowsGuest",
            )
        ),
        mount_tools_installer=AsyncMock(return_value=False),
        wait_for_tools=AsyncMock(),
    )
    ctx = context(request_for("blank", iso="iso-corp-windows-2025"), vmware)
    ctx.job.guest_os_status = GuestOsStatus.READY.value

    outcome = await stage_wait_for_tools(ctx)

    assert outcome.status == "WAITING_FOR_PREREQUISITE"
    assert outcome.artifacts["required_action"] == "PREPARE_CDROM_FOR_VMWARE_TOOLS"
    vmware.wait_for_tools.assert_not_awaited()


def _floppy_answer_file(content: bytes) -> str:
    start = content.index(b"<?xml")
    end = content.index(b"</unattend>") + len(b"</unattend>")
    return content[start:end].decode("utf-8")


@pytest.mark.asyncio
async def test_windows_template_gets_first_boot_media_that_never_touches_disks() -> None:
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(
            return_value=PowerStateInfo(
                power_state="poweredOff", configured_guest_id="windows2019srv_64Guest"
            )
        ),
        attach_temporary_floppy=AsyncMock(
            return_value=TemporaryMediaRef(datastore_path="[datastore] infraops-unattend/a.flp")
        ),
    )
    ctx = context(request_for("template"), vmware)
    ctx.job_id = uuid.uuid4()
    ctx.resolve_guest_credentials = AsyncMock(
        return_value=SimpleNamespace(username="Administrator", password="secret")
    )

    outcome = await stage_prepare_unattended_install(ctx)

    assert outcome.status == "SUCCEEDED"
    assert outcome.artifacts == {"datastore_path": "[datastore] infraops-unattend/a.flp"}
    xml = _floppy_answer_file(vmware.attach_temporary_floppy.await_args.kwargs["content"])
    assert 'pass="specialize"' in xml and 'pass="oobeSystem"' in xml
    assert f"<ComputerName>{ctx.request.effective_computer_name}</ComputerName>" in xml
    assert "<HideEULAPage>true</HideEULAPage>" in xml
    # A deployed image must never see Setup disk layout or an auto-logon.
    for forbidden in ('pass="windowsPE"', "WillWipeDisk", "DiskConfiguration", "AutoLogon"):
        assert forbidden not in xml


@pytest.mark.asyncio
async def test_non_windows_template_gets_no_answer_media() -> None:
    vmware = SimpleNamespace(
        get_vm_info=AsyncMock(
            return_value=PowerStateInfo(power_state="poweredOff", configured_guest_id="ubuntu64Guest")
        ),
        attach_temporary_floppy=AsyncMock(),
    )
    ctx = context(request_for("template"), vmware)
    ctx.resolve_guest_credentials = AsyncMock()

    outcome = await stage_prepare_unattended_install(ctx)

    assert outcome.status == "NOT_APPLICABLE"
    vmware.attach_temporary_floppy.assert_not_awaited()
    ctx.resolve_guest_credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_blank_iso_uses_answer_floppy_not_a_second_datastore_iso() -> None:
    vmware = SimpleNamespace(
        attach_temporary_floppy=AsyncMock(
            return_value=TemporaryMediaRef(
                datastore_path="[datastore] infraops-unattend/answer.flp"
            )
        )
    )
    ctx = context(request_for("blank", iso="iso-corp-windows-2025"), vmware)
    ctx.job_id = uuid.uuid4()
    ctx.resolve_guest_credentials = AsyncMock(
        return_value=SimpleNamespace(username="Administrator", password="secret")
    )

    outcome = await stage_prepare_unattended_install(ctx)

    assert outcome.status == "SUCCEEDED"
    call = vmware.attach_temporary_floppy.await_args
    assert call.kwargs["file_name"].endswith(".flp")
    assert len(call.kwargs["content"]) == 1_474_560


def _tools_running(host_name: str | None = None) -> PowerStateInfo:
    return PowerStateInfo(
        power_state="poweredOn",
        tools_status="toolsOk",
        tools_running_status="guestToolsRunning",
        guest_operations_ready=True,
        guest_family="windowsGuest",
        guest_host_name=host_name,
    )


def _setup_state(
    *,
    setup: int = 0,
    oobe: int = 0,
    image: str = "IMAGE_STATE_COMPLETE",
    name: str = "SERVER-PROD-042",
    sysprep: bool = False,
) -> CommandResult:
    payload = {
        "ImageState": image,
        "SystemSetupInProgress": setup,
        "OOBEInProgress": oobe,
        "ComputerName": name,
        "SysprepRunning": sysprep,
    }
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

    for name in (
        "_SETUP_POLL_SECONDS",
        "_SETUP_PROBE_SECONDS_NAME_APPLIED",
        "_SETUP_PROBE_SECONDS_OTHERWISE",
        "_SETUP_PROBE_SECONDS_AFTER_REJECTION",
        "_OOBE_STALL_SECONDS",
        "_SYSPREP_GRACE_SECONDS",
    ):
        monkeypatch.setattr(stages, name, 0.0)


def template_setup_context(vmware, probes: list) -> SimpleNamespace:
    ctx = context(request_for("template"), vmware)
    ctx.resolve_guest_credentials = AsyncMock(
        return_value=SimpleNamespace(username="Administrator", password="secret")
    )
    ctx.guest_ops = SimpleNamespace(
        run_powershell=AsyncMock(side_effect=probes), upload_file=AsyncMock()
    )
    return ctx


@pytest.mark.asyncio
async def test_template_wait_observes_existing_tools_without_mounting(fast_setup_polling) -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(),
        get_vm_info=AsyncMock(return_value=_tools_running("SERVER-PROD-042")),
    )
    ctx = template_setup_context(vmware, [_setup_state()])

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert vmware.wait_for_tools.await_args.kwargs["mount_if_missing"] is False
    assert ctx.job.guest_os_status == GuestOsStatus.READY.value
    assert "Setup has finished as 'SERVER-PROD-042'" in outcome.output


@pytest.mark.asyncio
async def test_tools_heartbeat_during_oobe_is_not_os_readiness(fast_setup_polling) -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(),
        get_vm_info=AsyncMock(return_value=_tools_running("SERVER-PROD-042")),
    )
    in_oobe = _setup_state(setup=1, oobe=1, image="IMAGE_STATE_SPECIALIZE_RESEAL_TO_OOBE")
    ctx = template_setup_context(vmware, [_rejected(), in_oobe, _setup_state()])

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert ctx.guest_ops.run_powershell.await_count == 3
    assert outcome.artifacts["windows_setup"]["image_state"] == "IMAGE_STATE_COMPLETE"


@pytest.mark.asyncio
async def test_no_guest_sign_in_is_attempted_while_tools_is_down(fast_setup_polling) -> None:
    import asyncio

    from app.workers.stages import wait_for_windows_setup

    restarting = PowerStateInfo(power_state="poweredOn", tools_running_status="guestToolsNotRunning")
    vmware = SimpleNamespace(get_vm_info=AsyncMock(return_value=restarting))
    ctx = template_setup_context(vmware, [])

    with pytest.raises(InfraOperationError, match="did not finish its first-boot setup") as caught:
        await wait_for_windows_setup(ctx, asyncio.get_running_loop().time() + 0.05)

    ctx.guest_ops.run_powershell.assert_not_awaited()
    assert "VMware Tools is not running" in caught.value.reason


@pytest.mark.asyncio
async def test_persistent_credential_rejection_stops_the_wait(fast_setup_polling) -> None:
    import asyncio

    from app.workers.stages import _SETUP_MAX_REJECTED_LOGINS, wait_for_windows_setup

    vmware = SimpleNamespace(get_vm_info=AsyncMock(return_value=_tools_running("TEMPLATE-NAME")))
    ctx = template_setup_context(vmware, [_rejected() for _ in range(_SETUP_MAX_REJECTED_LOGINS)])

    with pytest.raises(InfraOperationError, match="keeps rejecting"):
        await wait_for_windows_setup(ctx, asyncio.get_running_loop().time() + 60)

    assert ctx.guest_ops.run_powershell.await_count == _SETUP_MAX_REJECTED_LOGINS


def test_windows_setup_state_parsing() -> None:
    from app.workers.stages import parse_windows_setup_state

    assert parse_windows_setup_state(_setup_state().stdout).complete
    assert not parse_windows_setup_state(_setup_state(oobe=1).stdout).complete
    assert not parse_windows_setup_state(
        _setup_state(image="IMAGE_STATE_SPECIALIZE_RESEAL_TO_OOBE").stdout
    ).complete
    # Releases that do not record ImageState rely on the in-progress flags.
    assert parse_windows_setup_state(_setup_state(image="").stdout).complete
    with pytest.raises(ValueError):
        parse_windows_setup_state("not json")


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


JOB_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def clone_context(vmware) -> SimpleNamespace:
    ctx = context(request_for("template"), vmware)
    ctx.job_id = JOB_ID
    return ctx


@pytest.mark.asyncio
async def test_retry_resumes_only_a_vm_created_by_this_job() -> None:
    vmware = SimpleNamespace(
        find_vm_ownership=AsyncMock(
            return_value=VmOwnership(vm_id="vm-existing", name="SERVER-PROD-042", owner_job_id=str(JOB_ID))
        ),
        clone_from_template=AsyncMock(),
        create_blank_vm=AsyncMock(),
    )
    ctx = clone_context(vmware)

    outcome = await stage_clone_vm(ctx)

    assert outcome.status == "SKIPPED"
    assert outcome.artifacts["vm_id"] == "vm-existing"
    vmware.clone_from_template.assert_not_awaited()
    vmware.create_blank_vm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [None, "33333333-3333-4333-8333-333333333333"])
async def test_existing_vm_not_created_by_this_job_is_never_adopted(owner) -> None:
    from app.core.errors import InfraOperationError

    vmware = SimpleNamespace(
        find_vm_ownership=AsyncMock(
            return_value=VmOwnership(vm_id="vm-prod", name="SERVER-PROD-042", owner_job_id=owner)
        ),
        clone_from_template=AsyncMock(),
        create_blank_vm=AsyncMock(),
    )
    ctx = clone_context(vmware)

    with pytest.raises(InfraOperationError) as raised:
        await stage_clone_vm(ctx)

    assert raised.value.retryable is False
    assert "not created by this job" in raised.value.human_message
    vmware.clone_from_template.assert_not_awaited()
    vmware.create_blank_vm.assert_not_awaited()


@pytest.mark.asyncio
async def test_created_vm_carries_the_job_ownership_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.audit import recorder as recorder_module

    monkeypatch.setattr(
        recorder_module, "record_audit", lambda _db: SimpleNamespace(record=AsyncMock())
    )
    vmware = SimpleNamespace(
        find_vm_ownership=AsyncMock(return_value=None),
        clone_from_template=AsyncMock(return_value=VmRef(id="vm-new", name="SERVER-PROD-042")),
    )
    ctx = clone_context(vmware)
    ctx.db = object()
    ctx.actor_username = "operator"
    ctx.job.datacenter_name = "DC01"

    outcome = await stage_clone_vm(ctx)

    spec = vmware.clone_from_template.await_args.args[1]
    assert spec.job_id == str(JOB_ID)
    assert outcome.artifacts["owner_job_id"] == str(JOB_ID)


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


@pytest.mark.asyncio
async def test_static_address_taken_after_submission_blocks_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import InfraOperationError
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
    ctx = context(request_for("template"), vmware)

    with pytest.raises(InfraOperationError, match="already in use"):
        await assert_static_address_unclaimed(ctx, "10.20.30.45", 24)

    # The VM's own address (retry) is never reported as a conflict.
    vmware.get_vm_info.return_value = PowerStateInfo(power_state="poweredOn", ip_addresses=["10.20.30.45"])
    await assert_static_address_unclaimed(ctx, "10.20.30.45", 24)


@pytest.mark.asyncio
async def test_package_keeps_its_own_firmware() -> None:
    from app.workers.stages import stage_configure_hardware

    vmware = SimpleNamespace(configure_hardware=AsyncMock())
    template_ctx = context(request_for("template"), vmware)

    outcome = await stage_configure_hardware(template_ctx)

    # Switching BIOS/EFI under an installed Windows leaves it unbootable.
    assert vmware.configure_hardware.await_args.kwargs["firmware"] is None
    assert "inherited from the package" in outcome.output

    blank_ctx = context(request_for("blank", iso="iso-corp-windows-2025"), vmware)
    await stage_configure_hardware(blank_ctx)
    assert vmware.configure_hardware.await_args.kwargs["firmware"] is not None


def test_vm_info_reports_tools_host_name_and_configured_guest_os() -> None:
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
        config=SimpleNamespace(guestId="windows2019srv_64Guest"),
    )

    info = VsphereVMwareService._power_state_info(vm)

    assert info.guest_host_name == "TEST3"
    assert info.configured_guest_id == "windows2019srv_64Guest"
    assert info.configured_for_windows
    assert not PowerStateInfo(power_state="poweredOff", configured_guest_id="rhel9_64Guest").configured_for_windows


def _started() -> CommandResult:
    return CommandResult(exit_code=0, stdout="SYSPREP-STARTED", stderr="", duration_seconds=0.1)


@pytest.mark.asyncio
async def test_package_that_was_never_sealed_is_generalized_with_the_answer_file(
    fast_setup_polling,
) -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(),
        get_vm_info=AsyncMock(return_value=_tools_running("TEMPLATE-01")),
    )
    probes = [
        _setup_state(name="TEMPLATE-01"),  # finished Setup, template identity
        _started(),
        _setup_state(setup=1, oobe=1, image="IMAGE_STATE_SPECIALIZE_RESEAL_TO_OOBE"),
        _setup_state(),  # finished again, now with the requested name
    ]
    ctx = template_setup_context(vmware, probes)

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert outcome.artifacts["windows_setup"]["generalized_by_infraops"] is True
    upload = ctx.guest_ops.upload_file.await_args
    answer_path, answer = upload.args[4], upload.args[3].decode("utf-8")
    assert answer_path.startswith("C:\Windows\Temp\infraops-unattend-") and answer_path.endswith(".xml")
    assert "<ComputerName>SERVER-PROD-042</ComputerName>" in answer
    start_script = ctx.guest_ops.run_powershell.await_args_list[1].args[3]
    # Sysprep gets the answer file explicitly; Windows need not discover any media.
    for flag in ("'/generalize'", "'/oobe'", "'/reboot'", "'/unattend:'"):
        assert flag in start_script
    assert f"$answer = '{answer_path}'" in start_script


@pytest.mark.asyncio
async def test_sysprep_that_exits_without_restarting_reports_its_error_log(fast_setup_polling) -> None:
    import asyncio

    from app.workers.stages import wait_for_windows_setup

    vmware = SimpleNamespace(get_vm_info=AsyncMock(return_value=_tools_running("TEMPLATE-01")))
    error_log = CommandResult(
        exit_code=0,
        stdout="Error SYSPRP Package Contoso.App was installed for a user, but not provisioned.",
        stderr="",
        duration_seconds=0.1,
    )
    probes = [_setup_state(name="TEMPLATE-01"), _started(), _setup_state(name="TEMPLATE-01"), error_log]
    ctx = template_setup_context(vmware, probes)

    with pytest.raises(InfraOperationError, match="Sysprep could not generalize") as caught:
        await wait_for_windows_setup(ctx, asyncio.get_running_loop().time() + 60)

    assert "installed for a user, but not provisioned" in caught.value.technical_detail


@pytest.mark.asyncio
async def test_sealed_package_stuck_at_oobe_is_restarted_with_the_answer_file(fast_setup_polling) -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(),
        get_vm_info=AsyncMock(return_value=_tools_running("WIN-P80372UTDOL")),
    )
    at_oobe = _setup_state(
        setup=1, oobe=1, image="IMAGE_STATE_SPECIALIZE_RESEAL_TO_OOBE", name="WIN-P80372UTDOL"
    )
    probes = [
        at_oobe,
        _started(),
        _setup_state(setup=1, oobe=1, image="IMAGE_STATE_SPECIALIZE_RESEAL_TO_OOBE"),
        _setup_state(),
    ]
    ctx = template_setup_context(vmware, probes)

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.status == "SUCCEEDED"
    assert outcome.artifacts["windows_setup"]["generalized_by_infraops"] is True
    ctx.guest_ops.upload_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_package_already_personalized_is_not_generalized_again(fast_setup_polling) -> None:
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(),
        get_vm_info=AsyncMock(return_value=_tools_running("SERVER-PROD-042")),
    )
    ctx = template_setup_context(vmware, [_setup_state()])

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.artifacts["windows_setup"]["generalized_by_infraops"] is False
    ctx.guest_ops.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_mock_guest_completes_an_infraops_sysprep(fast_setup_polling) -> None:
    from app.services.guest.mock import MockGuestOperations, mock_guest_state, reset_mock_guests

    reset_mock_guests()
    mock_guest_state("SERVER-PROD-042").hostname = "TEMPLATE-01"
    vmware = SimpleNamespace(
        wait_for_tools=AsyncMock(),
        get_vm_info=AsyncMock(return_value=_tools_running("TEMPLATE-01")),
    )
    ctx = template_setup_context(vmware, [])
    ctx.target = SimpleNamespace(id="mock-vcenter")
    ctx.guest_ops = MockGuestOperations()

    outcome = await stage_wait_for_guest_os(ctx)

    assert outcome.artifacts["windows_setup"] == {
        "image_state": "IMAGE_STATE_COMPLETE",
        "computer_name": "SERVER-PROD-042",
        "generalized_by_infraops": True,
    }
    reset_mock_guests()
