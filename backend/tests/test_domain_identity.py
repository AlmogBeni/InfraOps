"""Domain joins keep Windows short names separate from DNS FQDNs."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import InfraOperationError
from app.schemas.provisioning import ProvisioningRequest
from app.services.guest import mock as guest_mock_module
from app.services.guest.base import CommandResult, GuestCredentials
from app.services.guest.mock import MockGuestOperations, reset_mock_guests
from app.services.vmware.base import PowerStateInfo, VCenterTarget
from app.workers import stages as stages_module
from app.workers.stages import (
    build_domain_join_script,
    build_windows_identity_probe_script,
    parse_windows_identity_state,
    stage_configure_hostname,
    stage_final_validation,
    stage_join_domain,
)
from tests.conftest import make_request


def domain_join_request() -> ProvisioningRequest:
    payload = make_request().model_dump(mode="json")
    payload["vm"]["name"] = "srvildc55"
    payload["guest"]["hostname"] = "LEGACY-OVERRIDE"
    payload["guest"]["domain_join"] = {
        "domain": "corp.deltagalil.com",
        "ou": "OU=Servers,DC=corp,DC=deltagalil,DC=com",
        "credential_secret_ref": "domain-join",
    }
    return ProvisioningRequest.model_validate(payload)


def identity_result(
    *,
    name: str,
    domain: str,
    part_of_domain: bool,
    active_name: str | None = None,
    pending_name: str | None = None,
    pending_domain_join: bool = False,
) -> CommandResult:
    return CommandResult(
        exit_code=0,
        stdout=json.dumps(
            {
                "Name": name,
                "Domain": domain,
                "PartOfDomain": part_of_domain,
                "ActiveName": active_name or name,
                "PendingName": pending_name or active_name or name,
                "PendingDomainJoin": pending_domain_join,
            }
        ),
        stderr="",
        duration_seconds=0.1,
    )


@pytest.mark.asyncio
async def test_domain_join_defers_rename_to_atomic_add_computer_operation() -> None:
    request = domain_join_request()
    run_program = AsyncMock()
    resolve_credentials = AsyncMock()
    ctx = SimpleNamespace(
        request=request,
        vm_name=request.vm.name,
        guest_ops=SimpleNamespace(run_program=run_program),
        target=object(),
        resolve_guest_credentials=resolve_credentials,
    )

    outcome = await stage_configure_hostname(ctx)

    assert outcome.status == "SKIPPED"
    assert outcome.artifacts == {
        "hostname": "SRVILDC55",
        "computer_name": "SRVILDC55",
        "requested_fqdn": "srvildc55.corp.deltagalil.com",
    }
    assert "applied atomically" in outcome.output
    run_program.assert_not_awaited()
    resolve_credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_computer_receives_short_vm_name_never_fqdn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = domain_join_request()
    run_program = AsyncMock(
        side_effect=[
            identity_result(
                name="TEMPLATE",
                domain="WORKGROUP",
                part_of_domain=False,
            ),
            CommandResult(
                exit_code=0,
                stdout="DOMAIN-JOINED",
                stderr="",
                duration_seconds=1.0,
            ),
        ]
    )

    async def get_secret(name: str) -> str:
        return "join-user" if name.endswith("/username") else "join-password"

    monkeypatch.setattr(
        stages_module,
        "effective_timeout_seconds",
        AsyncMock(return_value=600.0),
    )
    ctx = SimpleNamespace(
        request=request,
        vm_name=request.vm.name,
        secrets=SimpleNamespace(get_secret=get_secret),
        resolve_guest_credentials=AsyncMock(
            return_value=GuestCredentials(username="local-admin", password="local-password")
        ),
        guest_ops=SimpleNamespace(run_program=run_program),
        target=object(),
        reboot_required=False,
    )

    outcome = await stage_join_domain(ctx)

    arguments = run_program.await_args_list[1].args[4]
    assert "Add-Computer" in arguments
    assert "-DomainName 'corp.deltagalil.com'" in arguments
    assert "-NewName 'SRVILDC55'" in arguments
    assert "Rename-Computer" not in arguments
    assert "srvildc55.corp.deltagalil.com" not in arguments.lower()
    assert outcome.artifacts["computer_name"] == "SRVILDC55"
    assert outcome.artifacts["requested_fqdn"] == "srvildc55.corp.deltagalil.com"
    assert outcome.artifacts["identity_verified"] is False
    assert "srvildc55.corp.deltagalil.com" not in outcome.output
    assert ctx.reboot_required is True


@pytest.mark.asyncio
async def test_join_retry_is_noop_when_windows_already_reports_exact_identity() -> None:
    request = domain_join_request()
    run_program = AsyncMock(
        return_value=identity_result(
            name="SRVILDC55",
            domain="CORP.DELTAGALIL.COM",
            part_of_domain=True,
        )
    )
    get_secret = AsyncMock()
    ctx = SimpleNamespace(
        request=request,
        vm_name=request.vm.name,
        secrets=SimpleNamespace(get_secret=get_secret),
        resolve_guest_credentials=AsyncMock(
            return_value=GuestCredentials(username="local-admin", password="local-password")
        ),
        guest_ops=SimpleNamespace(run_program=run_program),
        target=object(),
        reboot_required=True,
    )

    outcome = await stage_join_domain(ctx)

    assert outcome.status == "SKIPPED"
    assert outcome.artifacts["identity_verified"] is True
    assert outcome.artifacts["fqdn"] == "srvildc55.corp.deltagalil.com"
    assert outcome.artifacts["reboot_required"] is False
    assert ctx.reboot_required is False
    get_secret.assert_not_awaited()
    assert run_program.await_count == 1


@pytest.mark.asyncio
async def test_join_retry_stops_on_pending_name_without_touching_ad() -> None:
    request = domain_join_request()
    run_program = AsyncMock(
        return_value=identity_result(
            name="TEMPLATE",
            domain="WORKGROUP",
            part_of_domain=False,
            active_name="TEMPLATE",
            pending_name="SRVILDC55",
        )
    )
    get_secret = AsyncMock()
    ctx = SimpleNamespace(
        request=request,
        vm_name=request.vm.name,
        secrets=SimpleNamespace(get_secret=get_secret),
        resolve_guest_credentials=AsyncMock(
            return_value=GuestCredentials(username="local-admin", password="local-password")
        ),
        guest_ops=SimpleNamespace(run_program=run_program),
        target=object(),
        reboot_required=False,
    )

    with pytest.raises(InfraOperationError, match="pending Windows identity change") as error:
        await stage_join_domain(ctx)

    assert error.value.retryable is True
    assert "Restart the guest" in error.value.recommended_action
    get_secret.assert_not_awaited()
    assert run_program.await_count == 1


@pytest.mark.asyncio
async def test_join_retry_stops_on_pending_domain_join_without_touching_ad() -> None:
    request = domain_join_request()
    run_program = AsyncMock(
        return_value=identity_result(
            name="SRVILDC55",
            domain="corp.deltagalil.com",
            part_of_domain=True,
            pending_domain_join=True,
        )
    )
    get_secret = AsyncMock()
    ctx = SimpleNamespace(
        request=request,
        vm_name=request.vm.name,
        secrets=SimpleNamespace(get_secret=get_secret),
        resolve_guest_credentials=AsyncMock(
            return_value=GuestCredentials(username="local-admin", password="local-password")
        ),
        guest_ops=SimpleNamespace(run_program=run_program),
        target=object(),
        reboot_required=False,
    )

    with pytest.raises(InfraOperationError, match="pending Windows identity change") as error:
        await stage_join_domain(ctx)

    assert error.value.retryable is True
    get_secret.assert_not_awaited()
    assert run_program.await_count == 1


@pytest.mark.asyncio
async def test_mock_guest_probe_tracks_join_as_pending_until_reboot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_mock_guests()
    monkeypatch.setattr(guest_mock_module, "_LATENCY_PROGRAM", 0)
    guest_ops = MockGuestOperations()
    target = VCenterTarget(
        id="mock-domain-identity",
        name="Mock vCenter",
        host="vcsa.example.test",
        port=443,
        username_secret_ref="vcenter/username",
        password_secret_ref="vcenter/password",
        verify_ssl=True,
    )
    credentials = GuestCredentials(username="local-admin", password="local-password")

    async def run(script: str) -> CommandResult:
        return await guest_ops.run_program(
            target,
            "template-vm",
            credentials,
            "powershell.exe",
            f"-NoProfile -NonInteractive -Command {script}",
            60,
        )

    initial = parse_windows_identity_state(
        (await run(build_windows_identity_probe_script())).stdout
    )
    assert initial.name == "TEMPLATE-VM"
    assert initial.part_of_domain is False
    assert initial.pending_domain_join is False

    await run(
        build_domain_join_script(
            "corp.example.com",
            "join-user",
            "join-password",
            None,
            "SRVILDC55",
        )
    )
    pending = parse_windows_identity_state(
        (await run(build_windows_identity_probe_script())).stdout
    )
    assert pending.name == "TEMPLATE-VM"
    assert pending.pending_name == "SRVILDC55"
    assert pending.part_of_domain is False
    assert pending.pending_domain_join is True

    await run("Restart-Computer -Force")
    observed = parse_windows_identity_state(
        (await run(build_windows_identity_probe_script())).stdout
    )
    assert observed.name == "SRVILDC55"
    assert observed.domain == "corp.example.com"
    assert observed.part_of_domain is True
    assert observed.pending_domain_join is False


def final_validation_context(identity: CommandResult) -> SimpleNamespace:
    request = domain_join_request()
    return SimpleNamespace(
        request=request,
        vm_name=request.vm.name,
        vmware=SimpleNamespace(
            get_vm_info=AsyncMock(
                return_value=PowerStateInfo(
                    power_state="poweredOn",
                    tools_status="toolsOk",
                    ip_addresses=[request.network.ipv4.address],
                )
            )
        ),
        guest_ops=SimpleNamespace(run_program=AsyncMock(return_value=identity)),
        resolve_guest_credentials=AsyncMock(
            return_value=GuestCredentials(username="local-admin", password="local-password")
        ),
        target=object(),
        steps_by_key={
            "configure_guest_network": SimpleNamespace(
                artifacts={
                    "gateway": request.network.ipv4.gateway,
                    "dns_servers": request.network.ipv4.dns_servers,
                }
            ),
            # A stale success artifact must not determine final identity status.
            "join_domain": SimpleNamespace(
                artifacts={"joined": True, "fqdn": "forged.example.test"}
            ),
        },
    )


@pytest.mark.asyncio
async def test_final_validation_emits_fqdn_only_from_observed_matching_identity() -> None:
    ctx = final_validation_context(
        identity_result(
            name="SRVILDC55",
            domain="corp.deltagalil.com",
            part_of_domain=True,
        )
    )

    outcome = await stage_final_validation(ctx)

    identity_check = next(
        item for item in outcome.artifacts["checklist"] if item["group"] == "Identity"
    )
    assert identity_check["status"] == "PASS"
    assert outcome.artifacts["summary"]["computer_name"] == "SRVILDC55"
    assert outcome.artifacts["summary"]["fqdn"] == "srvildc55.corp.deltagalil.com"
    assert "forged.example.test" not in str(outcome.artifacts)


@pytest.mark.asyncio
async def test_final_validation_rejects_stale_join_artifact_when_observation_mismatches() -> None:
    ctx = final_validation_context(
        identity_result(
            name="OTHER-SERVER",
            domain="corp.deltagalil.com",
            part_of_domain=True,
        )
    )

    with pytest.raises(InfraOperationError, match="Final validation reported") as error:
        await stage_final_validation(ctx)

    assert "Identity/Observed Windows identity" in error.value.reason
    assert "forged.example.test" not in error.value.technical_detail
