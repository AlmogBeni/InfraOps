"""Pipeline stage implementations for VM provisioning.

Each handler receives the :class:`JobRunContext`, performs exactly one
concern, and returns a :class:`StageOutcome`. Handlers raise
:class:`InfraOperationError` on failure — the pipeline converts that into the
human/technical error pair persisted on the job step.

Every script reaching a guest is assembled exclusively from typed, validated
values (IP octets, enum stores, GUIDs, server-generated paths) quoted as
PowerShell literals, uploaded as a ``.ps1`` file and run with ``-File`` —
never through ``cmd.exe``. Credentials are never part of a script or command
line: they are delivered as self-deleting secret files (see
:mod:`app.services.guest.scripts`) and are never logged or persisted.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actions import AuditAction
from app.core.errors import InfraOperationError
from app.core.logging import get_logger
from app.models.applications import Application
from app.models.certificates import Certificate, CertificatePackage
from app.models.jobs import (
    GuestOsStatus,
    GuestProvisioningStatus,
    InfrastructureStatus,
    VMwareToolsStatus,
)
from app.schemas.provisioning import IpMode, VmSourceType
from app.services.applications.installer import ApplicationDefinition
from app.services.applications.paths import path_within_roots
from app.services.applications.resolver import AppNode, resolve_install_order
from app.services.certificates.deployer import CertificateDeployer, CertificateToDeploy
from app.services.guest.base import GuestCredentialsRejected
from app.services.guest.scripts import HOSTNAME_PATH, new_temp_path, new_token
from app.services.guest.scripts import ps_quote as ps_single_quote
from app.services.settings_store import (
    SETTING_ALLOWED_INSTALLER_ROOTS,
    SETTING_VM_NAME_POLICY,
    load_effective,
)
from app.services.vmware.base import PowerStateInfo, VmRef
from app.services.windows_unattend import (
    WindowsFirstBootSpec,
    WindowsUnattendSpec,
    build_autounattend_xml,
    build_first_boot_unattend_xml,
    build_unattend_floppy,
)
from app.workers.context import JobRunContext
from app.workers.state_machine import ORDERED_STAGES

log = get_logger(__name__)

SHUTDOWN_PATH = r"C:\Windows\System32\shutdown.exe"


@dataclass
class StageOutcome:
    status: str = "SUCCEEDED"
    output: str = ""
    artifacts: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WindowsIdentityState:
    name: str
    domain: str
    part_of_domain: bool
    active_name: str
    pending_name: str
    pending_domain_join: bool

    @property
    def has_pending_rename(self) -> bool:
        return bool(
            self.active_name
            and self.pending_name
            and self.active_name.casefold() != self.pending_name.casefold()
        )

    @property
    def fqdn(self) -> str | None:
        if not self.part_of_domain or not self.name or not self.domain:
            return None
        return f"{self.name}.{self.domain}".lower()


StageHandler = Callable[[JobRunContext], Awaitable[StageOutcome]]


# ── pure script builders (unit-testable) ─────────────────────────────────────

_SELECT_ADAPTER = (
    "$adapter = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | Select-Object -First 1\n"
    "if (-not $adapter) { throw 'No connected network adapter found' }\n"
)


def build_static_ip_script(address: str, prefix: int, gateway: str, dns_servers: list[str]) -> str:
    """Idempotent static IPv4 configuration (safe to re-run on retry)."""
    if dns_servers:
        dns_line = (
            "Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses "
            + ",".join(ps_single_quote(d) for d in dns_servers)
            + " | Out-Null\n"
        )
    else:
        dns_line = "Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ResetServerAddresses | Out-Null\n"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + _SELECT_ADAPTER
        + "Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Dhcp Disabled\n"
        "Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |\n"
        "    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue\n"
        "Get-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |\n"
        "    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue\n"
        f"New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress {ps_single_quote(address)} "
        f"-PrefixLength {int(prefix)} -DefaultGateway {ps_single_quote(gateway)} | Out-Null\n"
        + dns_line
        + "'NETWORK-CONFIGURED'"
    )


def build_dhcp_script() -> str:
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + _SELECT_ADAPTER
        + "Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Dhcp Enabled\n"
        "Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ResetServerAddresses | Out-Null\n"
        "'NETWORK-DHCP-ENABLED'"
    )


def build_dhcp_probe_script() -> str:
    return (
        "$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {\n"
        "    $_.IPAddress -notlike '169.254*' -and $_.IPAddress -ne '127.0.0.1'\n"
        "} | Select-Object -First 1).IPAddress\n"
        "if ($ip) { \"DHCP-IP:$ip\" } else { exit 1 }"
    )


def build_rename_script(new_name: str) -> str:
    return f"Rename-Computer -NewName {ps_single_quote(new_name)} -Force -ErrorAction Stop | Out-Null\n'RENAMED'"


def build_windows_identity_probe_script() -> str:
    return (
        "$cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop; "
        "$active = (Get-ItemProperty -LiteralPath "
        "'Registry::HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\ComputerName\\"
        "ActiveComputerName' -ErrorAction SilentlyContinue).ComputerName; "
        "$pending = (Get-ItemProperty -LiteralPath "
        "'Registry::HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\ComputerName\\"
        "ComputerName' -ErrorAction SilentlyContinue).ComputerName; "
        "$netlogon = 'Registry::HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\Netlogon'; "
        "$pendingJoin = ((Test-Path -LiteralPath ($netlogon + '\\JoinDomain')) -or "
        "(Test-Path -LiteralPath ($netlogon + '\\AvoidSpnSet'))); "
        "[pscustomobject]@{Name=[string]$cs.Name;Domain=[string]$cs.Domain;"
        "PartOfDomain=[bool]$cs.PartOfDomain;ActiveName=[string]$active;"
        "PendingName=[string]$pending;PendingDomainJoin=[bool]$pendingJoin} | "
        "ConvertTo-Json -Compress"
    )


def parse_windows_identity_state(stdout: str) -> WindowsIdentityState:
    payload = None
    for line in reversed([entry.strip() for entry in stdout.splitlines() if entry.strip()]):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "PartOfDomain" in candidate:
            payload = candidate
            break
    if payload is None:
        raise ValueError("The Windows identity probe did not return a JSON object.")

    raw_membership = payload.get("PartOfDomain")
    if isinstance(raw_membership, bool):
        part_of_domain = raw_membership
    elif isinstance(raw_membership, str) and raw_membership.casefold() in {"true", "false"}:
        part_of_domain = raw_membership.casefold() == "true"
    else:
        raise ValueError("The Windows identity probe returned an invalid PartOfDomain value.")

    name = str(payload.get("Name") or "").strip()
    domain = str(payload.get("Domain") or "").strip().rstrip(".")
    if not name or not domain:
        raise ValueError("The Windows identity probe omitted Name or Domain.")
    active_name = str(payload.get("ActiveName") or name).strip()
    pending_name = str(payload.get("PendingName") or active_name).strip()
    raw_pending_join = payload.get("PendingDomainJoin", False)
    if isinstance(raw_pending_join, bool):
        pending_domain_join = raw_pending_join
    elif isinstance(raw_pending_join, str) and raw_pending_join.casefold() in {"true", "false"}:
        pending_domain_join = raw_pending_join.casefold() == "true"
    else:
        raise ValueError("The Windows identity probe returned an invalid PendingDomainJoin value.")
    return WindowsIdentityState(
        name=name,
        domain=domain,
        part_of_domain=part_of_domain,
        active_name=active_name,
        pending_name=pending_name,
        pending_domain_join=pending_domain_join,
    )


def windows_identity_matches(
    state: WindowsIdentityState,
    *,
    computer_name: str,
    domain: str,
) -> bool:
    return (
        state.part_of_domain
        and state.name.casefold() == computer_name.casefold()
        and state.domain.casefold() == domain.rstrip(".").casefold()
        and not state.has_pending_rename
        and not state.pending_domain_join
    )


def windows_identity_detail(state: WindowsIdentityState) -> str:
    membership = "domain member" if state.part_of_domain else "not domain joined"
    return (
        f"Name={state.name}, Domain={state.domain}, PartOfDomain={state.part_of_domain}, "
        f"ActiveName={state.active_name}, PendingName={state.pending_name}, "
        f"PendingDomainJoin={state.pending_domain_join} ({membership})"
    )


async def probe_windows_identity(
    ctx: JobRunContext,
    credentials,
    *,
    operation: str,
) -> WindowsIdentityState:
    result = await ctx.guest_ops.run_powershell(
        ctx.target,
        ctx.vm_name,
        credentials,
        build_windows_identity_probe_script(),
        60,
    )
    if not result.succeeded:
        raise InfraOperationError(
            "The Windows computer identity could not be read safely.",
            reason=f"The identity probe failed before {operation}.",
            recommended_action="Verify VMware Tools and local administrator access, then retry.",
            technical_detail=(result.stdout + "\n" + result.stderr)[-1500:],
            retryable=True,
        )
    try:
        return parse_windows_identity_state(result.stdout)
    except ValueError as exc:
        raise InfraOperationError(
            "The Windows computer identity could not be interpreted safely.",
            reason=str(exc),
            recommended_action="Retry after confirming the guest reports its Windows name and domain state.",
            technical_detail=result.stdout[-1500:],
            retryable=True,
        ) from exc


@dataclass(frozen=True)
class WindowsSetupState:
    image_state: str
    system_setup_in_progress: bool
    oobe_in_progress: bool
    computer_name: str
    sysprep_running: bool = False

    @property
    def complete(self) -> bool:
        # Older releases may not record ImageState; the in-progress flags
        # alone then decide.
        return (
            not self.system_setup_in_progress
            and not self.oobe_in_progress
            and self.image_state in ("", "IMAGE_STATE_COMPLETE")
        )

    @property
    def detail(self) -> str:
        return (
            f"ImageState={self.image_state or 'unrecorded'}, "
            f"SystemSetupInProgress={int(self.system_setup_in_progress)}, "
            f"OOBEInProgress={int(self.oobe_in_progress)}, ComputerName={self.computer_name}"
        )


def build_windows_setup_state_script() -> str:
    """Read-only probe of Windows Setup progress (specialize / OOBE)."""
    return (
        "$setup = Get-ItemProperty -LiteralPath "
        "'Registry::HKEY_LOCAL_MACHINE\\SYSTEM\\Setup' -ErrorAction Stop; "
        "$image = (Get-ItemProperty -LiteralPath "
        "'Registry::HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Setup\\State' "
        "-ErrorAction SilentlyContinue).ImageState; "
        "$sysprep = [bool](Get-Process -Name sysprep -ErrorAction SilentlyContinue); "
        "[pscustomobject]@{ImageState=[string]$image;"
        "SystemSetupInProgress=[int]$setup.SystemSetupInProgress;"
        "OOBEInProgress=[int]$setup.OOBEInProgress;"
        "ComputerName=[string]$env:COMPUTERNAME;SysprepRunning=$sysprep} | ConvertTo-Json -Compress"
    )


SYSPREP_PATH = r"C:\Windows\System32\Sysprep\sysprep.exe"
SYSPREP_ERROR_LOG = r"C:\Windows\System32\Sysprep\Panther\setuperr.log"


def build_start_sysprep_script(answer_path: str) -> str:
    """Generalize this copy with an explicit answer file.

    Windows does not reliably discover answer media on the first boot of a
    generalized image, so InfraOps passes its answer file to Sysprep itself.
    Sysprep reboots the guest, so it is started detached and never awaited.
    """
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"$answer = {ps_single_quote(answer_path)}\n"
        "if (-not (Test-Path -LiteralPath $answer)) { throw 'The InfraOps answer file is missing.' }\n"
        "if (Get-Process -Name sysprep -ErrorAction SilentlyContinue) { 'SYSPREP-ALREADY-RUNNING'; exit 0 }\n"
        f"Start-Process -FilePath {ps_single_quote(SYSPREP_PATH)} -ArgumentList @("
        "'/generalize', '/oobe', '/reboot', '/quiet', ('/unattend:' + $answer)) | Out-Null\n"
        "'SYSPREP-STARTED'"
    )


def build_sysprep_error_script() -> str:
    return (
        f"$log = {ps_single_quote(SYSPREP_ERROR_LOG)}\n"
        "if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 40 } "
        "else { 'Sysprep wrote no error log.' }"
    )


def parse_windows_setup_state(stdout: str) -> WindowsSetupState:
    for line in reversed([entry.strip() for entry in stdout.splitlines() if entry.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "SystemSetupInProgress" in payload:
            try:
                return WindowsSetupState(
                    image_state=str(payload.get("ImageState") or "").strip().upper(),
                    system_setup_in_progress=int(payload.get("SystemSetupInProgress") or 0) != 0,
                    oobe_in_progress=int(payload.get("OOBEInProgress") or 0) != 0,
                    computer_name=str(payload.get("ComputerName") or "").strip(),
                    sysprep_running=payload.get("SysprepRunning") in (True, "True", "true"),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("The Windows Setup probe returned non-numeric flags.") from exc
    raise ValueError("The Windows Setup probe did not return a JSON object.")


DOMAIN_JOIN_PASSWORD_SECRET = "domain_join_password"


def build_domain_join_script(
    domain: str,
    username: str,
    ou: str | None,
    new_name: str | None,
) -> str:
    """Add-Computer script. The password is read from a self-deleting secret
    file (``$InfraOpsSecrets``) and never appears in the script text, on a
    command line, or in PowerShell script-block logs.

    ``new_name`` must be None when Windows already has that name: Add-Computer
    skips the whole join ("the new name is the same as the current name")
    when -NewName equals the current name.
    """
    ou_clause = f" -OUPath {ps_single_quote(ou)}" if ou else ""
    rename_clause = f" -NewName {ps_single_quote(new_name)}" if new_name else ""
    return (
        "$secpw = ConvertTo-SecureString "
        f"$InfraOpsSecrets[{ps_single_quote(DOMAIN_JOIN_PASSWORD_SECRET)}] -AsPlainText -Force\n"
        f"$InfraOpsSecrets.Remove({ps_single_quote(DOMAIN_JOIN_PASSWORD_SECRET)})\n"
        "$cred = New-Object System.Management.Automation.PSCredential("
        f"{ps_single_quote(username)}, $secpw)\n"
        f"Add-Computer -DomainName {ps_single_quote(domain)}"
        f"{rename_clause}{ou_clause} -Credential $cred "
        "-Force -ErrorAction Stop | Out-Null\n'DOMAIN-JOINED'"
    )


# ── helpers ──────────────────────────────────────────────────────────────────

_TIMEOUT_KEY_MAP: dict[str, tuple[str, ...]] = {
    "clone_vm": ("clone_minutes",),
    "wait_for_guest_os": ("vmware_tools_minutes",),
    "wait_for_tools": ("vmware_tools_minutes",),
    "configure_guest_network": ("network_configuration_minutes",),
    "validate_network": ("network_configuration_minutes",),
    "join_domain": ("guest_operations_minutes",),
    "reboot_guest": ("guest_operations_minutes",),
    "wait_guest_ready": ("guest_operations_minutes",),
}


async def effective_timeout_seconds(ctx: JobRunContext, stage_key: str) -> float:
    from app.workers.state_machine import DEFAULT_STAGE_TIMEOUTS, stage_timeout

    override: dict[str, int] = {}
    try:
        rows = await load_effective(ctx.db)
        raw = rows.get("default_timeouts")
        if isinstance(raw, dict):
            override = {k: int(v) for k, v in raw.items()}
    except Exception:  # noqa: BLE001 - fall back to defaults when settings unavailable
        pass
    mapped = stage_timeout(stage_key, DEFAULT_STAGE_TIMEOUTS)
    for setting_key in _TIMEOUT_KEY_MAP.get(stage_key, ()):
        if setting_key in override:
            configured = float(override[setting_key]) * 60
            if (
                stage_key in ("wait_for_guest_os", "wait_for_tools")
                and ctx.request.source_type == VmSourceType.BLANK
                and ctx.request.guest.iso_id is not None
            ):
                return max(configured, 7200.0)
            if stage_key == "wait_for_guest_os" and ctx.request.source_type == VmSourceType.TEMPLATE:
                # First boot of a generalized package: specialize, reboot, OOBE.
                return max(configured, 3600.0)
            return configured
    return float(mapped)


def _require_vm_id(ctx: JobRunContext) -> str:
    if ctx.vm_ref is None:
        step = ctx.steps_by_key.get("clone_vm")
        stored = (step.artifacts or {}).get("vm_id") if step is not None else None
        if stored:
            ctx.vm_ref = VmRef(id=stored, name=ctx.vm_name)
            return stored
        raise InfraOperationError(
            "The created VM reference is missing.",
            reason="The VM creation stage has not produced a VM identifier.",
            recommended_action="Retry the 'Create virtual machine' stage.",
            retryable=True,
        )
    return ctx.vm_ref.id


def _blank_guest_skip(ctx: JobRunContext, operation: str) -> StageOutcome | None:
    if (
        ctx.request.source_type != VmSourceType.BLANK
        or ctx.request.guest.iso_id is not None
    ):
        return None
    return StageOutcome(
        status="NOT_APPLICABLE",
        output=(
            f"{operation} is not applicable to a blank VM. The VM is left powered off "
            "until an operating system is installed."
        ),
    )


def _tools_lifecycle(info: PowerStateInfo | None) -> VMwareToolsStatus:
    if info is None:
        return VMwareToolsStatus.UNKNOWN

    version = info.tools_version_status
    running = info.tools_running_status
    if version == "guestToolsNotInstalled":
        return VMwareToolsStatus.NOT_INSTALLED
    if running == "guestToolsExecutingScripts":
        return VMwareToolsStatus.INSTALLING
    if running == "guestToolsNotRunning":
        return VMwareToolsStatus.NOT_RUNNING
    if running == "guestToolsRunning":
        if version in {
            "guestToolsNeedUpgrade",
            "guestToolsTooOld",
            "guestToolsSupportedOld",
            "guestToolsBlacklisted",
        }:
            return VMwareToolsStatus.OUTDATED
        return VMwareToolsStatus.RUNNING

    return {
        "toolsOk": VMwareToolsStatus.RUNNING,
        "toolsOld": VMwareToolsStatus.OUTDATED,
        "toolsNotRunning": VMwareToolsStatus.NOT_RUNNING,
        "toolsNotInstalled": VMwareToolsStatus.NOT_INSTALLED,
    }.get(info.tools_status, VMwareToolsStatus.UNKNOWN)


async def _load_certificates(
    db: AsyncSession, package_ids: list[uuid.UUID], certificate_type: str
) -> list[CertificateToDeploy]:
    if not package_ids:
        return []
    result = await db.execute(
        select(Certificate)
        .join(CertificatePackage, Certificate.package_id == CertificatePackage.id)
        .where(
            Certificate.package_id.in_(package_ids),
            Certificate.enabled.is_(True),
            CertificatePackage.enabled.is_(True),
            Certificate.certificate_type == certificate_type,
        )
        .order_by(Certificate.friendly_name)
    )
    return [
        CertificateToDeploy(
            friendly_name=cert.friendly_name,
            certificate_type=cert.certificate_type,
            thumbprint=cert.fingerprint_sha256,
            pem_body=cert.pem_body,
            not_after=cert.not_after,
        )
        for cert in result.scalars().all()
    ]


async def _load_application_catalog(db: AsyncSession) -> dict[uuid.UUID, AppNode]:
    result = await db.execute(select(Application))
    catalog: dict[uuid.UUID, AppNode] = {}
    for app in result.scalars().all():
        catalog[app.id] = AppNode(
            id=app.id,
            name=app.name,
            enabled=app.enabled,
            dependency_ids=frozenset(dep.depends_on_id for dep in app.dependencies),
        )
    return catalog


def _definition_from_orm(app: Application) -> ApplicationDefinition:
    return ApplicationDefinition(
        id=app.id,
        name=app.name,
        installer_type=app.installer_type,
        installer_path=app.installer_path,
        install_arguments=app.install_arguments,
        detection_method=app.detection_method.value,
        detection_config=dict(app.detection_config or {}),
        timeout_seconds=app.timeout_seconds,
        reboot_required=app.reboot_required,
    )


# ── stage handlers ───────────────────────────────────────────────────────────

async def stage_validate_request(ctx: JobRunContext) -> StageOutcome:
    ctx.job.infrastructure_status = InfrastructureStatus.PENDING.value
    if ctx.request.source_type == VmSourceType.BLANK:
        ctx.job.guest_os_status = (
            GuestOsStatus.UNKNOWN.value
            if ctx.request.guest.iso_id
            else GuestOsStatus.NOT_PRESENT.value
        )
        ctx.job.vmware_tools_status = VMwareToolsStatus.NOT_APPLICABLE_YET.value
    rows = await load_effective(ctx.db)
    policy = str(rows.get(SETTING_VM_NAME_POLICY) or "")
    if policy and re.fullmatch(policy, ctx.vm_name) is None:
        raise InfraOperationError(
            f"The VM name '{ctx.vm_name}' violates the configured naming policy.",
            reason=f"Naming policy regex: {policy}",
            recommended_action="Choose a compliant VM name and resubmit.",
            retryable=False,
        )
    from app.models.jobs import JobStatus, ProvisioningJob

    conflict = await ctx.db.execute(
        select(ProvisioningJob.id).where(
            ProvisioningJob.vm_name == ctx.vm_name,
            ProvisioningJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            ProvisioningJob.id != ctx.job_id,
        )
    )
    if conflict.first() is not None:
        raise InfraOperationError(
            f"Another provisioning job for '{ctx.vm_name}' is already active.",
            reason="Duplicate active job for the same VM name.",
            recommended_action="Wait for the existing job to finish or choose another name.",
            retryable=False,
        )
    r = ctx.request
    if r.application_ids:
        selected = await ctx.db.execute(
            select(Application).where(Application.id.in_(r.application_ids))
        )
        applications = list(selected.scalars().all())
        allowed_roots = [str(root) for root in rows.get(SETTING_ALLOWED_INSTALLER_ROOTS, [])]
        outside = [
            app.installer_path
            for app in applications
            if not path_within_roots(app.installer_path, allowed_roots)
        ]
        if not allowed_roots or outside:
            raise InfraOperationError(
                "Application installer repository policy is not satisfied.",
                reason=(
                    "No approved installer roots are configured."
                    if not allowed_roots
                    else f"Installer paths outside approved roots: {', '.join(outside)}"
                ),
                recommended_action=(
                    "Configure approved installer roots and correct the application catalog "
                    "before resubmitting."
                ),
                retryable=False,
            )
    return StageOutcome(
        output=(
            f"Request validated.\n"
            f"Source: {r.source_type.value}\n"
            f"VM: {r.vm.name}\nInfrastructure placement supplied.\n"
            f"CPU/Memory: {r.hardware.cpu} vCPU / {r.hardware.memory_mb} MB\n"
            f"Disks: {len(r.hardware.disks)}\nMode: {r.network.mode.value}"
        ),
        artifacts={"validated_at": dt.datetime.now(dt.UTC).isoformat()},
    )


async def stage_connect_vcenter(ctx: JobRunContext) -> StageOutcome:
    result = await ctx.vmware.test_connection(ctx.target)
    if not result.ok:
        raise InfraOperationError(
            f"Could not connect to vCenter '{ctx.target.host}'.",
            reason=result.detail or "Connection test failed.",
            recommended_action="Check the vCenter connection under Administration → VMware Connections.",
            retryable=True,
        )
    return StageOutcome(output=f"Connected to {ctx.target.host} ({result.latency_ms or '?'} ms).")


async def stage_validate_infrastructure(ctx: JobRunContext) -> StageOutcome:
    r = ctx.request
    problems: list[str] = []

    datacenters = {dc.id: dc for dc in await ctx.vmware.get_datacenters(ctx.target)}
    dc = datacenters.get(r.compute.datacenter_id)
    if dc is None:
        problems.append(f"datacenter '{r.compute.datacenter_id}' missing")
    else:
        # Persist the human-readable inventory name once vCenter has confirmed
        # it. Logs and audit responses join this stable job context rather than
        # presenting a raw managed-object reference as the primary label.
        ctx.job.datacenter_id = dc.id
        ctx.job.datacenter_name = dc.name
    clusters = (
        {c.id: c for c in await ctx.vmware.get_clusters(ctx.target, r.compute.datacenter_id)}
        if dc else {}
    )
    cluster = clusters.get(r.compute.cluster_id)
    if cluster is None:
        problems.append(f"compute target '{r.compute.cluster_id}' missing")

    datastores = {}
    if cluster is not None:
        hosts = {h.id: h for h in await ctx.vmware.get_hosts(ctx.target, cluster.id)}
        if r.compute.host_id:
            host = hosts.get(r.compute.host_id)
            if host is None or not host.available_for_provisioning:
                problems.append(f"host '{r.compute.host_id}' unavailable")
        if r.compute.resource_pool_id:
            pools = {p.id for p in await ctx.vmware.get_resource_pools(ctx.target, cluster.id)}
            if r.compute.resource_pool_id not in pools:
                problems.append("resource pool does not belong to the cluster")
        datastores = {d.id: d for d in await ctx.vmware.get_datastores(ctx.target, cluster.id)}
        required_gb = r.total_disk_gb
        explicit = {d.datastore_id for d in r.hardware.disks if d.datastore_id}
        if explicit:
            for ds_id in explicit:
                ds = datastores.get(ds_id)
                if ds is None or not ds.accessible:
                    problems.append(f"datastore '{ds_id}' inaccessible")
                elif ds.free_gb < required_gb:
                    problems.append(
                        f"datastore '{ds.name}' has {ds.free_gb:.0f} GB free, needs ~{required_gb} GB"
                    )
        else:
            best = max((d.free_gb for d in datastores.values() if d.accessible), default=0.0)
            if best < required_gb:
                problems.append(f"no datastore with >= {required_gb} GB free (best {best:.0f} GB)")

    networks = {
        network.id: network
        for network in (
            await ctx.vmware.get_networks(ctx.target, r.compute.datacenter_id)
            if dc is not None
            else []
        )
    }
    network = networks.get(r.network.network_id)
    if network is None:
        problems.append(f"network '{r.network.network_id}' missing")

    if r.source_type == VmSourceType.TEMPLATE:
        templates = {
            t.id: t
            for t in await ctx.vmware.get_templates(ctx.target, r.compute.datacenter_id)
        }
        if r.guest.template_id not in templates:
            problems.append(f"template '{r.guest.template_id}' missing")
    elif r.guest.iso_id:
        isos = {
            image.id: image
            for image in await ctx.vmware.get_isos(ctx.target, r.compute.datacenter_id)
        }
        image = isos.get(r.guest.iso_id)
        if image is None:
            problems.append("selected ISO missing from the datacenter")
        elif image.datastore_id not in datastores:
            problems.append("selected ISO datastore unavailable to the cluster")

    if problems:
        raise InfraOperationError(
            "The infrastructure configuration could not be re-validated on vCenter.",
            reason="; ".join(problems),
            recommended_action="Re-open the wizard, refresh the affected selections and resubmit.",
            technical_detail=json.dumps(problems),
            retryable=True,
        )
    source_detail = " and template" if r.source_type == VmSourceType.TEMPLATE else ""
    return StageOutcome(
        output=f"Datacenter, cluster, placement, storage, network{source_detail} verified.",
        artifacts={
            "datacenter_name": dc.name if dc is not None else None,
            "network_name": network.name if network is not None else None,
        },
    )


async def stage_clone_vm(ctx: JobRunContext) -> StageOutcome:
    from app.audit.recorder import record_audit
    from app.services.vmware.base import BlankVmSpec, CloneSpec, VmRef

    r = ctx.request
    job_marker = str(ctx.job_id)
    ctx.job.infrastructure_status = InfrastructureStatus.CREATING.value
    existing = await ctx.vmware.find_vm_ownership(ctx.target, ctx.vm_name)
    if existing is not None:
        if existing.owner_job_id != job_marker:
            # Never adopt, reconfigure or power on a VM this job did not create.
            ctx.job.infrastructure_status = InfrastructureStatus.FAILED.value
            raise InfraOperationError(
                f"A virtual machine named '{ctx.vm_name}' already exists and was not created by this job.",
                reason=(
                    "The VM name is taken by an existing VM"
                    + (
                        f" created by another InfraOps job ({existing.owner_job_id})."
                        if existing.owner_job_id
                        else " that carries no InfraOps ownership marker."
                    )
                ),
                recommended_action=(
                    "Choose a different VM name and submit a new request. InfraOps did not modify "
                    "the existing VM."
                ),
                technical_detail=f"vm_id={existing.vm_id} owner={existing.owner_job_id!r} job={job_marker}",
                retryable=False,
            )
        ctx.vm_ref = VmRef(id=existing.vm_id, name=existing.name)
        ctx.job.infrastructure_status = InfrastructureStatus.READY.value
        return StageOutcome(
            status="SKIPPED",
            output=(
                f"VM '{ctx.vm_name}' was already created by this job (ownership marker verified) — "
                "resuming with it instead of creating it again."
            ),
            artifacts={"vm_id": existing.vm_id, "vm_name": existing.name, "owner_job_id": job_marker},
        )

    datastore_id = next((d.datastore_id for d in r.hardware.disks if d.datastore_id), None)
    if r.source_type == VmSourceType.TEMPLATE:
        spec = CloneSpec(
            template_id=r.guest.template_id or "",
            vm_name=r.vm.name,
            datacenter_id=r.compute.datacenter_id,
            description=r.vm.description,
            cluster_id=r.compute.cluster_id,
            host_id=r.compute.host_id,
            resource_pool_id=r.compute.resource_pool_id,
            datastore_id=datastore_id,
            cpu=r.hardware.cpu,
            memory_mb=r.hardware.memory_mb,
            disks=tuple(r.hardware.disks),
            network_id=r.network.network_id,
            adapter_type=r.network.adapter_type,
            firmware=r.hardware.firmware,
            secure_boot=r.hardware.secure_boot,
            job_id=job_marker,
        )
        vm_ref = await ctx.vmware.clone_from_template(ctx.target, spec)
        output = f"Deployed '{vm_ref.name}' from the selected OVF/OVA package."
    else:
        blank_spec = BlankVmSpec(
            vm_name=r.vm.name,
            datacenter_id=r.compute.datacenter_id,
            description=r.vm.description,
            cluster_id=r.compute.cluster_id,
            host_id=r.compute.host_id,
            resource_pool_id=r.compute.resource_pool_id,
            datastore_id=datastore_id,
            cpu=r.hardware.cpu,
            memory_mb=r.hardware.memory_mb,
            disks=tuple(r.hardware.disks),
            firmware=r.hardware.firmware,
            secure_boot=r.hardware.secure_boot,
            iso_id=r.guest.iso_id,
            job_id=job_marker,
        )
        vm_ref = await ctx.vmware.create_blank_vm(ctx.target, blank_spec)
        media = " with the selected ISO mounted" if r.guest.iso_id else " without installation media"
        output = f"Created blank virtual machine '{vm_ref.name}'{media} in powered-off state."
    ctx.vm_ref = vm_ref
    ctx.job.infrastructure_status = InfrastructureStatus.READY.value
    if r.source_type == VmSourceType.BLANK and r.guest.iso_id is None:
        ctx.job.guest_os_status = GuestOsStatus.INSTALLATION_REQUIRED.value
        ctx.job.vmware_tools_status = VMwareToolsStatus.NOT_APPLICABLE_YET.value
        ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_OS.value
    await record_audit(ctx.db).record(
        AuditAction.VM_CREATED,
        resource_type="virtual_machine",
        resource_name=vm_ref.name,
        job_id=ctx.job_id,
        username=ctx.actor_username,
        datacenter_id=r.compute.datacenter_id,
        datacenter_name=ctx.job.datacenter_name,
        result="success",
        details={
            "computer_name": r.effective_computer_name or None,
            "requested_fqdn": r.effective_fqdn,
            "source_type": r.source_type.value,
            "template_id": r.guest.template_id,
            "iso_id": r.guest.iso_id,
            "vm_id": vm_ref.id,
        },
    )
    return StageOutcome(
        output=output,
        artifacts={"vm_id": vm_ref.id, "vm_name": vm_ref.name, "owner_job_id": job_marker},
    )


async def stage_configure_hardware(ctx: JobRunContext) -> StageOutcome:
    vm_id = _require_vm_id(ctx)
    hw = ctx.request.hardware
    # An installed package boots only with the firmware it was installed
    # under; switching BIOS/EFI would leave it unbootable.
    from_package = ctx.request.source_type == VmSourceType.TEMPLATE
    await ctx.vmware.configure_hardware(
        ctx.target, vm_id,
        cpu=hw.cpu, memory_mb=hw.memory_mb, disks=list(hw.disks),
        firmware=None if from_package else hw.firmware, secure_boot=hw.secure_boot,
    )
    disk_summary = ", ".join(f"{d.size_gb} GB {d.provisioning.value}" for d in hw.disks)
    firmware = (
        "inherited from the package"
        if from_package
        else f"{hw.firmware.value}{' + Secure Boot' if hw.secure_boot else ''}"
    )
    return StageOutcome(
        output=f"CPU: {hw.cpu} vCPU\nMemory: {hw.memory_mb} MB\nFirmware: {firmware}\nDisks: {disk_summary}",
    )


async def stage_attach_network_adapter(ctx: JobRunContext) -> StageOutcome:
    vm_id = _require_vm_id(ctx)
    net = ctx.request.network
    await ctx.vmware.attach_network(
        ctx.target,
        vm_id,
        net.network_id,
        net.adapter_type,
        ctx.request.compute.datacenter_id,
    )
    return StageOutcome(output=f"{net.adapter_type.value} network adapter connected.")


def _unusable_setup_credential(exc: ValueError) -> InfraOperationError:
    return InfraOperationError(
        "The provisioning credential cannot be used by Windows Setup.",
        reason=str(exc),
        recommended_action=(
            "Select a credential whose username is a local account such as 'Administrator' "
            "(no domain prefix), then submit a new request."
        ),
        retryable=False,
    )


def first_boot_answer_xml(ctx: JobRunContext, credentials) -> bytes:
    """Answer file for specialize + OOBE of a deployed Windows package."""
    guest = ctx.request.guest
    try:
        return build_first_boot_unattend_xml(
            WindowsFirstBootSpec(
                computer_name=ctx.request.effective_computer_name,
                administrator_username=credentials.username,
                administrator_password=credentials.password,
                locale=guest.installation_locale,
                input_locale=guest.input_locale,
                timezone=guest.timezone or "UTC",
            )
        )
    except ValueError as exc:
        raise _unusable_setup_credential(exc) from exc


async def stage_prepare_unattended_install(ctx: JobRunContext) -> StageOutcome:
    request = ctx.request
    if request.source_type == VmSourceType.BLANK and request.guest.iso_id is None:
        return StageOutcome(
            status="NOT_APPLICABLE",
            output="No operating system is installed on a blank VM without an ISO.",
        )
    vm_id = _require_vm_id(ctx)
    guest = request.guest
    if request.source_type == VmSourceType.TEMPLATE:
        info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
        if info is None or not info.configured_for_windows:
            configured = (info.configured_guest_id if info else None) or "unknown"
            return StageOutcome(
                status="NOT_APPLICABLE",
                output=(
                    f"The package is configured for guest OS '{configured}', not Windows, so no "
                    "Windows answer media was attached."
                ),
            )
        if info.power_state == "poweredOn":
            return StageOutcome(
                status="SKIPPED",
                output="The VM is already running, so its first boot has already happened.",
            )

    credentials = await ctx.resolve_guest_credentials()
    if request.source_type == VmSourceType.TEMPLATE:
        xml = first_boot_answer_xml(ctx, credentials)
        summary = (
            "Temporary first-boot answer media attached (computer name, time zone, locale, "
            "keyboard layout, administrator password, every OOBE page skipped). After the first "
            "boot InfraOps also runs Sysprep with the same answer file inside the guest, so the "
            "deployment does not depend on Windows discovering this media."
        )
    else:
        try:
            xml = build_autounattend_xml(
                WindowsUnattendSpec(
                    computer_name=request.effective_computer_name,
                    administrator_username=credentials.username,
                    administrator_password=credentials.password,
                    image_index=guest.windows_image_index,
                    locale=guest.installation_locale,
                    input_locale=guest.input_locale,
                    timezone=guest.timezone or "UTC",
                    firmware=request.hardware.firmware.value,
                )
            )
        except ValueError as exc:
            raise _unusable_setup_credential(exc) from exc
        summary = (
            "Temporary answer media attached. Windows Setup will configure the selected "
            "administrator, locale, keyboard layout and computer name without OOBE prompts."
        )
    # The media contains a plaintext Windows Setup password by necessity. It is
    # held only in memory here, uploaded directly, and removed once Windows
    # Setup has finished (or when the job stops earlier).
    media = build_unattend_floppy(xml)
    datastore_id = next(
        (disk.datastore_id for disk in ctx.request.hardware.disks if disk.datastore_id),
        None,
    )
    ref = await ctx.vmware.attach_temporary_floppy(
        ctx.target,
        vm_id,
        datacenter_id=ctx.request.compute.datacenter_id,
        datastore_id=datastore_id,
        file_name=f"infraops-{ctx.job_id}.flp",
        content=media,
    )
    return StageOutcome(output=summary, artifacts={"datastore_path": ref.datastore_path})


# USB HID usage ID of the space bar.
_HID_SPACEBAR = 0x2C
# Windows installation media boots only after "Press any key to boot from CD or
# DVD", shown for about five seconds right after the firmware starts.
_BOOT_KEY_SECONDS = 20.0
_BOOT_KEY_INTERVAL_SECONDS = 1.0


async def press_key_to_boot_from_iso(ctx: JobRunContext, vm_id: str) -> int:
    loop = asyncio.get_running_loop()
    stop_at = loop.time() + _BOOT_KEY_SECONDS
    sent = 0
    while True:
        try:
            sent += await ctx.vmware.send_keystrokes(ctx.target, vm_id, [_HID_SPACEBAR])
        except InfraOperationError as exc:
            raise InfraOperationError(
                "Windows Setup could not be started from the ISO.",
                reason=(
                    "The installation media waits for a key press, and vCenter refused the "
                    f"keystroke: {exc.human_message}"
                ),
                recommended_action=(
                    "Grant the vCenter service account 'Virtual machine > Interaction > Inject USB "
                    "HID scan codes', then retry this stage (the VM is reset and the key is sent again)."
                ),
                technical_detail=exc.technical_detail,
                retryable=True,
            ) from exc
        if loop.time() >= stop_at:
            return sent
        await asyncio.sleep(_BOOT_KEY_INTERVAL_SECONDS)


async def stage_power_on(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Power-on")
    if skipped:
        return skipped
    vm_id = _require_vm_id(ctx)
    from_iso = ctx.request.source_type == VmSourceType.BLANK and ctx.request.guest.iso_id is not None
    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    already_on = info is not None and info.power_state == "poweredOn"
    if already_on and not from_iso:
        return StageOutcome(status="SKIPPED", output="VM is already powered on.")
    if not from_iso:
        await ctx.vmware.power_on(ctx.target, vm_id)
        return StageOutcome(output="Power-on task completed.")

    # This stage reruns only after it failed, so Setup cannot have started:
    # restart the firmware to get the boot prompt back.
    if already_on:
        await ctx.vmware.reset(ctx.target, vm_id)
    else:
        await ctx.vmware.power_on(ctx.target, vm_id)
    sent = await press_key_to_boot_from_iso(ctx, vm_id)
    return StageOutcome(
        output=(
            f"{'Reset' if already_on else 'Power-on'} task completed. The space bar was pressed "
            f"{sent} time(s) during the first {int(_BOOT_KEY_SECONDS)} seconds to answer the "
            "installation media's 'Press any key to boot from CD or DVD' prompt."
        ),
        artifacts={"boot_keys_sent": sent},
    )


async def _wait_for_installation_from_iso(ctx: JobRunContext, vm_id: str) -> StageOutcome:
    """Windows installs from the ISO, then installs VMware Tools at first logon
    from the host's Tools ISO; its heartbeat is the first sign of the new OS."""
    ctx.job.guest_os_status = GuestOsStatus.INSTALLATION_IN_PROGRESS.value
    ctx.job.vmware_tools_status = VMwareToolsStatus.NOT_APPLICABLE_YET.value
    ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_OS.value
    loop = asyncio.get_running_loop()
    timeout = await effective_timeout_seconds(ctx, "wait_for_guest_os")
    deadline = loop.time() + max(60.0, timeout - 30.0)
    try:
        await ctx.vmware.wait_for_tools(
            ctx.target, vm_id, max(1.0, deadline - loop.time() - 120.0), mount_if_missing=False
        )
    except InfraOperationError as exc:
        raise InfraOperationError(
            "Windows did not finish installing from the ISO in time.",
            reason=(
                "VMware Tools never reported from the new installation, so Windows Setup or the "
                "first-logon VMware Tools installation did not complete."
            ),
            recommended_action=(
                "Open the VM console. An EFI boot list or 'Press any key to boot from CD or DVD' "
                "means Setup never started; a Windows Setup error usually points at the image index "
                "or the ISO; a Windows desktop without VMware Tools means the host's Tools ISO "
                "([] /vmimages/tools-isoimages/windows.iso) was not available on the second CD "
                "drive. Fix the cause and redeploy."
            ),
            technical_detail=exc.technical_detail,
            retryable=True,
        ) from exc
    first_boot = await wait_for_windows_setup(ctx, deadline)
    state = first_boot.state
    ctx.job.guest_os_status = GuestOsStatus.READY.value
    ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_TOOLS.value
    return StageOutcome(
        output=(
            f"Windows was installed unattended from the ISO; Setup has finished as "
            f"'{state.computer_name}' and the provisioning account signs in through VMware Tools "
            f"({first_boot.probes} probe(s)).\n{state.detail}"
        ),
        artifacts={
            "windows_setup": {
                "image_state": state.image_state,
                "computer_name": state.computer_name,
                "generalized_by_infraops": first_boot.generalized_by_infraops,
            }
        },
    )


async def stage_wait_for_guest_os(ctx: JobRunContext) -> StageOutcome:
    """Prove guest readiness without equating VM existence or power with an OS."""
    vm_id = _require_vm_id(ctx)
    request = ctx.request

    if request.source_type == VmSourceType.BLANK and request.guest.iso_id is None:
        step = ctx.steps_by_key.get("wait_for_guest_os")
        confirmed = bool((step.artifacts or {}).get("administrator_confirmed")) if step else False
        if confirmed:
            ctx.job.guest_os_status = GuestOsStatus.READY.value
            ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_TOOLS.value
            return StageOutcome(
                output="An administrator confirmed that the guest OS is installed and booted."
            )
        ctx.job.guest_os_status = GuestOsStatus.INSTALLATION_REQUIRED.value
        ctx.job.vmware_tools_status = VMwareToolsStatus.NOT_APPLICABLE_YET.value
        ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_OS.value
        return StageOutcome(
            status="WAITING_FOR_PREREQUISITE",
            output=(
                "VM hardware was created successfully. No operating system is installed by "
                "InfraOps because no ISO was selected. Attach installation media, install and boot "
                "the guest OS, then confirm readiness. VMware Tools and guest configuration are waiting."
            ),
            artifacts={"required_action": "INSTALL_AND_CONFIRM_GUEST_OS"},
        )

    if request.source_type == VmSourceType.BLANK:
        return await _wait_for_installation_from_iso(ctx, vm_id)

    timeout = await effective_timeout_seconds(ctx, "wait_for_guest_os")
    # Finish with a clear error before the pipeline's own stage timeout fires.
    deadline = asyncio.get_running_loop().time() + max(60.0, timeout - 30.0)
    # An OVF/OVA deploy result proves only that the vCenter resource exists.
    # Wait for an existing heartbeat and never mount/reinstall Tools silently.
    try:
        await ctx.vmware.wait_for_tools(
            ctx.target, vm_id, max(1.0, min(timeout, 900.0) - 5.0), mount_if_missing=False
        )
    except InfraOperationError:
        info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
        tools = _tools_lifecycle(info)
        ctx.job.guest_os_status = GuestOsStatus.UNKNOWN.value
        ctx.job.vmware_tools_status = tools.value
        ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_TOOLS.value
        return StageOutcome(
            status="WAITING_FOR_PREREQUISITE",
            output=(
                "The OVF/OVA resource was deployed, but InfraOps cannot verify a ready guest because "
                "VMware Tools/open-vm-tools has no heartbeat. Verify that the package contains a "
                "bootable OS and start or install its supported Tools implementation."
            ),
            artifacts={"required_action": "VERIFY_GUEST_OS_AND_TOOLS"},
        )
    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    if info is not None and info.guest_family and "windows" not in info.guest_family.casefold():
        ctx.job.guest_os_status = GuestOsStatus.READY.value
        ctx.job.guest_provisioning_status = GuestProvisioningStatus.FAILED.value
        return StageOutcome(
            status="WAITING_FOR_PREREQUISITE",
            output=(
                f"The package booted a '{info.guest_family}' guest. This InfraOps workflow only "
                "implements Windows guest commands, so Windows networking/domain/application "
                "steps were not offered to the guest. Deploy it without Windows customization "
                "when that capability is added, or choose a prepared Windows package."
            ),
            artifacts={"required_action": "SELECT_SUPPORTED_WINDOWS_PACKAGE"},
        )
    # A VMware Tools heartbeat also appears during specialize and OOBE of a
    # generalized package, so it does not prove the OS is ready.
    ctx.job.guest_os_status = GuestOsStatus.INSTALLATION_IN_PROGRESS.value
    first_boot = await wait_for_windows_setup(ctx, deadline)
    state = first_boot.state
    ctx.job.guest_os_status = GuestOsStatus.READY.value
    ctx.job.guest_provisioning_status = GuestProvisioningStatus.IN_PROGRESS.value
    how = (
        "InfraOps generalized this copy with Sysprep and its answer file"
        if first_boot.generalized_by_infraops
        else "Windows already reports the requested computer name"
    )
    return StageOutcome(
        output=(
            f"{how}; Setup has finished as '{state.computer_name}' and the provisioning account "
            f"signs in through VMware Tools ({first_boot.probes} probe(s)).\n{state.detail}"
        ),
        artifacts={
            "windows_setup": {
                "image_state": state.image_state,
                "computer_name": state.computer_name,
                "generalized_by_infraops": first_boot.generalized_by_infraops,
            }
        },
    )


_SETUP_POLL_SECONDS = 10.0
# Sign-ins are spaced out while Windows Setup may not have applied the
# provisioning password yet: after a rejection the next attempt waits long
# enough that failures stay below the default lockout threshold (10 bad
# sign-ins within 10 minutes on current Windows releases).
_SETUP_PROBE_SECONDS_NAME_APPLIED = 15.0
_SETUP_PROBE_SECONDS_OTHERWISE = 60.0
_SETUP_PROBE_SECONDS_AFTER_REJECTION = 75.0
_SETUP_MAX_REJECTED_LOGINS = 12
# A sealed package still parked at OOBE without the requested name after this
# long did not pick up the answer media; Sysprep restarts it explicitly.
_OOBE_STALL_SECONDS = 120.0
# Sysprep reboots the guest within minutes; exiting without doing so means
# it failed.
_SYSPREP_GRACE_SECONDS = 90.0


@dataclass(frozen=True)
class WindowsFirstBoot:
    state: WindowsSetupState
    probes: int
    generalized_by_infraops: bool


async def _start_sysprep(ctx: JobRunContext, credentials) -> None:
    """Upload the answer file and start ``sysprep /generalize /oobe /reboot``."""
    answer_path = new_temp_path(f"unattend-{new_token()}", "xml")
    # Holds the administrator password until the cleanup stage deletes it;
    # files an administrator creates in C:\Windows\Temp are not readable by users.
    await ctx.guest_ops.upload_file(
        ctx.target, ctx.vm_name, credentials, first_boot_answer_xml(ctx, credentials), answer_path
    )
    result = await ctx.guest_ops.run_powershell(
        ctx.target, ctx.vm_name, credentials, build_start_sysprep_script(answer_path), 120
    )
    if not result.succeeded:
        raise InfraOperationError(
            "Sysprep could not be started in the deployed VM.",
            reason=f"The start command exited with code {result.exit_code}.",
            recommended_action="Check that the provisioning account is a local administrator, then retry.",
            technical_detail=(result.stdout + "\n" + result.stderr)[-1500:],
            retryable=True,
        )


async def _sysprep_failure(ctx: JobRunContext, credentials) -> InfraOperationError:
    try:
        result = await ctx.guest_ops.run_powershell(
            ctx.target, ctx.vm_name, credentials, build_sysprep_error_script(), 60
        )
        log_tail = result.stdout.strip() or "Sysprep wrote no error log."
    except InfraOperationError as exc:
        log_tail = f"The Sysprep error log could not be read: {exc.human_message}"
    return InfraOperationError(
        "Sysprep could not generalize the deployed copy of the package.",
        reason="Sysprep exited without restarting Windows; its error log is in the technical output.",
        recommended_action=(
            "Fix the reported problem in the template, republish it and redeploy. Common causes are "
            "pending updates or a pending restart, apps installed for a single user, a domain-joined "
            "template, or the Sysprep generalize limit."
        ),
        technical_detail=f"{SYSPREP_ERROR_LOG}:\n{log_tail}"[-4000:],
        retryable=True,
    )


async def wait_for_windows_setup(ctx: JobRunContext, deadline: float) -> WindowsFirstBoot:
    """Bring a deployed Windows package to a finished, personalized Setup.

    Readiness is proven inside the guest: the provisioning account signs in
    through VMware Tools, the registry shows no Setup or OOBE in progress and
    Windows reports the requested computer name. A package that has not been
    generalized with the InfraOps answer file is generalized here, with the
    answer file passed to Sysprep explicitly.
    """
    loop = asyncio.get_running_loop()
    desired = ctx.request.effective_computer_name.casefold()
    credentials = await ctx.resolve_guest_credentials()
    next_probe = loop.time()
    probes = 0
    rejected = 0
    sysprep_started_at: float | None = None
    # Set once Windows has left its finished state after Sysprep started
    # (restart, specialize or OOBE observed).
    setup_seen_since_sysprep = False
    oobe_stalled_since: float | None = None
    last = "VMware Tools has not reported yet."

    def has_requested_name(state: WindowsSetupState) -> bool:
        return bool(desired) and state.computer_name.casefold() == desired

    async def start_sysprep(reason: str) -> None:
        nonlocal sysprep_started_at, setup_seen_since_sysprep, rejected, next_probe, last
        await _start_sysprep(ctx, credentials)
        sysprep_started_at = loop.time()
        setup_seen_since_sysprep = False
        rejected = 0
        next_probe = loop.time() + _SETUP_PROBE_SECONDS_NAME_APPLIED
        last = reason

    while loop.time() < deadline:
        info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
        tools_running = info is not None and info.guest_operations_ready and (
            info.tools_running_status == "guestToolsRunning"
            or info.tools_status in ("toolsOk", "toolsOld")
        )
        if not tools_running:
            if sysprep_started_at is not None:
                setup_seen_since_sysprep = True
            last = "VMware Tools is not running (Windows restarts during Sysprep and Setup)."
        elif loop.time() >= next_probe:
            reported = (info.guest_host_name or "").split(".", 1)[0].casefold()
            name_applied = bool(desired) and reported == desired
            next_probe = loop.time() + (
                _SETUP_PROBE_SECONDS_NAME_APPLIED if name_applied else _SETUP_PROBE_SECONDS_OTHERWISE
            )
            probes += 1
            try:
                result = await ctx.guest_ops.run_powershell(
                    ctx.target, ctx.vm_name, credentials, build_windows_setup_state_script(), 60
                )
            except GuestCredentialsRejected as exc:
                rejected += 1
                next_probe = loop.time() + _SETUP_PROBE_SECONDS_AFTER_REJECTION
                last = (
                    "Windows rejected the provisioning account (expected until OOBE applies its "
                    f"password; {rejected} rejection(s))."
                )
                if rejected >= _SETUP_MAX_REJECTED_LOGINS:
                    raise InfraOperationError(
                        "Windows keeps rejecting the provisioning account.",
                        reason=(
                            f"{rejected} sign-in attempts through VMware Tools were rejected while "
                            "waiting for Windows Setup."
                        ),
                        recommended_action=(
                            "If the VM console shows the Windows 'Hi there' (OOBE) page, the package "
                            "was sealed with Sysprep and its administrator password no longer works, "
                            "so InfraOps cannot finish Setup. Publish the template without running "
                            "Sysprep (InfraOps generalizes every deployment itself) with its local "
                            "Administrator password set to the selected provisioning credential. "
                            "Otherwise, correct the credential and retry this stage."
                        ),
                        technical_detail=exc.technical_detail,
                        retryable=True,
                    ) from exc
            except InfraOperationError as exc:
                last = exc.human_message
            else:
                if not result.succeeded:
                    last = f"The Windows Setup probe exited with code {result.exit_code}."
                else:
                    try:
                        state = parse_windows_setup_state(result.stdout)
                    except ValueError as exc:
                        last = str(exc)
                    else:
                        now = loop.time()
                        if sysprep_started_at is not None and not state.complete:
                            setup_seen_since_sysprep = True
                        if (
                            state.complete
                            and has_requested_name(state)
                            and (sysprep_started_at is None or setup_seen_since_sysprep)
                        ):
                            return WindowsFirstBoot(state, probes, sysprep_started_at is not None)
                        if sysprep_started_at is not None:
                            if state.complete and setup_seen_since_sysprep:
                                raise InfraOperationError(
                                    "Windows Setup finished without applying the InfraOps answer file.",
                                    reason=f"Windows reports {state.detail}.",
                                    recommended_action=(
                                        "Check C:\\Windows\\Panther\\setuperr.log in the VM, fix the "
                                        "template and redeploy."
                                    ),
                                    retryable=True,
                                )
                            if (
                                state.complete
                                and not state.sysprep_running
                                and now - sysprep_started_at >= _SYSPREP_GRACE_SECONDS
                            ):
                                raise await _sysprep_failure(ctx, credentials)
                            last = (
                                "Sysprep is generalizing Windows."
                                if state.complete
                                else f"Windows Setup is running with the InfraOps answer file ({state.detail})."
                            )
                        elif state.complete:
                            # Not generalized with our answer file (a template that
                            # was never sealed): give this copy its own identity.
                            await start_sysprep("Sysprep started with the InfraOps answer file.")
                        elif state.oobe_in_progress and not has_requested_name(state):
                            oobe_stalled_since = oobe_stalled_since if oobe_stalled_since is not None else now
                            last = f"Windows is waiting at OOBE without the answer media ({state.detail})."
                            if now - oobe_stalled_since >= _OOBE_STALL_SECONDS:
                                await start_sysprep(
                                    "Windows stopped at OOBE without the answer media; Sysprep "
                                    "restarted Setup with the InfraOps answer file."
                                )
                        else:
                            oobe_stalled_since = None
                            last = f"Windows Setup is still running ({state.detail})."
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(_SETUP_POLL_SECONDS, remaining))
    raise InfraOperationError(
        "Windows did not finish its first-boot setup in time.",
        reason=f"Last observation: {last}",
        recommended_action=(
            "Open the VM console in vCenter. If Windows shows an OOBE page, an unanswered page "
            "(such as a product key prompt) stopped Setup; fix the template and redeploy. If Setup "
            "is still progressing, retry this stage."
        ),
        technical_detail=(
            f"probes={probes} rejected_logins={rejected} "
            f"sysprep_started={sysprep_started_at is not None}"
        ),
        retryable=True,
    )


async def stage_wait_for_tools(ctx: JobRunContext) -> StageOutcome:
    vm_id = _require_vm_id(ctx)
    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    state = _tools_lifecycle(info)
    ctx.job.vmware_tools_status = state.value
    if state == VMwareToolsStatus.RUNNING:
        return StageOutcome(output="VMware Tools is installed, current, and running.")
    if state == VMwareToolsStatus.OUTDATED:
        return StageOutcome(
            status="WARNING",
            output=(
                "VMware Tools is running but outdated. InfraOps continued with a warning and did "
                "not silently upgrade the guest."
            ),
        )
    if (
        ctx.request.source_type == VmSourceType.BLANK
        and ctx.request.guest.iso_id is not None
        and state in (VMwareToolsStatus.UNKNOWN, VMwareToolsStatus.NOT_INSTALLED)
    ):
        try:
            mounted = await ctx.vmware.mount_tools_installer(ctx.target, vm_id)
        except InfraOperationError as exc:
            ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_TOOLS.value
            return StageOutcome(
                status="WAITING_FOR_PREREQUISITE",
                output=(
                    "The guest OS is ready, but vCenter could not supply VMware Tools media. "
                    "Disconnect the Windows installation ISO or provide an available CD/DVD "
                    "device, then resume."
                ),
                artifacts={
                    "required_action": "PREPARE_CDROM_FOR_VMWARE_TOOLS",
                    "last_observation": exc.human_message,
                },
            )
        if mounted:
            ctx.job.vmware_tools_status = VMwareToolsStatus.INSTALLING.value
            timeout = await effective_timeout_seconds(ctx, "wait_for_tools")
            try:
                await ctx.vmware.wait_for_tools(
                    ctx.target,
                    vm_id,
                    max(1.0, timeout - 5.0),
                    mount_if_missing=False,
                )
            except InfraOperationError as exc:
                ctx.job.guest_provisioning_status = (
                    GuestProvisioningStatus.WAITING_FOR_TOOLS.value
                )
                return StageOutcome(
                    status="WAITING_FOR_PREREQUISITE",
                    output=(
                        "VMware Tools media was supplied only after OS readiness was confirmed, "
                        "but no Tools heartbeat was observed. Run or troubleshoot the installer "
                        "inside Windows, then resume."
                    ),
                    artifacts={
                        "required_action": "COMPLETE_VMWARE_TOOLS_INSTALLATION",
                        "last_observation": exc.human_message,
                    },
                )
            ctx.job.vmware_tools_status = VMwareToolsStatus.RUNNING.value
            ctx.job.guest_provisioning_status = GuestProvisioningStatus.IN_PROGRESS.value
            return StageOutcome(
                output="VMware Tools was installed inside Windows and its heartbeat is ready."
            )
        ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_TOOLS.value
        return StageOutcome(
            status="WAITING_FOR_PREREQUISITE",
            output=(
                "The guest OS is ready, but vCenter could not mount VMware Tools media. "
                "Disconnect the Windows installation ISO from the CD/DVD device, then resume."
            ),
            artifacts={"required_action": "PREPARE_CDROM_FOR_VMWARE_TOOLS"},
        )
    ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_TOOLS.value
    if state == VMwareToolsStatus.NOT_RUNNING:
        message = (
            "VMware Tools is installed but not running. Start or troubleshoot the guest service; "
            "InfraOps will not reinstall it blindly."
        )
    else:
        ctx.job.vmware_tools_status = VMwareToolsStatus.NOT_INSTALLED.value
        message = (
            "VMware Tools/open-vm-tools is not installed or has never reported. Install the "
            "guest-appropriate implementation inside the confirmed OS, then resume. Merely "
            "mounting the Tools ISO is not installation."
        )
    return StageOutcome(
        status="WAITING_FOR_PREREQUISITE",
        output=message,
        artifacts={"required_action": "MAKE_VMWARE_TOOLS_READY"},
    )


ANSWER_FILE_SCRUB_SCRIPT = (
    "$paths = @(\n"
    "    'C:\\Windows\\Panther\\unattend.xml',\n"
    "    'C:\\Windows\\Panther\\Unattend\\unattend.xml',\n"
    "    'C:\\Windows\\Panther\\Unattend\\autounattend.xml',\n"
    "    'C:\\Windows\\Panther\\autounattend.xml',\n"
    "    'C:\\Windows\\System32\\Sysprep\\unattend.xml',\n"
    "    'C:\\Windows\\System32\\Sysprep\\Panther\\unattend.xml'\n"
    ")\n"
    "foreach ($path in $paths) {\n"
    "    if (Test-Path -LiteralPath $path) {\n"
    "        Remove-Item -LiteralPath $path -Force -ErrorAction Stop\n"
    "        \"REMOVED $path\"\n"
    "    }\n"
    "}\n"
    "$winlogon = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon'\n"
    "Get-ChildItem -LiteralPath 'C:\\Windows\\Temp' -Filter 'infraops-unattend-*.xml' "
    "-ErrorAction SilentlyContinue | ForEach-Object {\n"
    "    Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop\n"
    "    \"REMOVED $($_.FullName)\"\n"
    "}\n"
    "Remove-ItemProperty -Path $winlogon -Name DefaultPassword -ErrorAction SilentlyContinue\n"
    "Set-ItemProperty -Path $winlogon -Name AutoAdminLogon -Value '0' -ErrorAction SilentlyContinue\n"
    "'ANSWER-FILE-SCRUBBED'"
)


def _uses_unattended_media(ctx: JobRunContext) -> bool:
    if ctx.request.source_type == VmSourceType.BLANK:
        return ctx.request.guest.iso_id is not None
    # Templates receive first-boot media only when configured for Windows.
    prepare = ctx.steps_by_key.get("prepare_unattended_install")
    return bool(prepare is not None and (prepare.artifacts or {}).get("datastore_path"))


async def release_unattended_media(ctx: JobRunContext, *, reason: str) -> bool:
    """Detach and delete the answer-file floppy on a failure/cancel/interrupt path.

    The floppy holds the local administrator password in plain text, so it
    must never outlive a job that stops before the cleanup stage. When the VM
    has not booted from it yet, the preparation stage is reset so a retry
    regenerates the media. Returns True when media was removed.
    """
    from app.models.jobs import StepStatus

    prepare = ctx.steps_by_key.get("prepare_unattended_install")
    cleanup = ctx.steps_by_key.get("cleanup_unattended_media")
    if prepare is None:
        return False
    artifacts = dict(prepare.artifacts or {})
    datastore_path = artifacts.get("datastore_path")
    if not datastore_path or artifacts.get("media_removed"):
        return False
    if cleanup is not None and cleanup.status == StepStatus.SUCCEEDED:
        return False
    try:
        vm_id = _require_vm_id(ctx)
        await asyncio.wait_for(
            ctx.vmware.remove_temporary_floppy(
                ctx.target,
                vm_id,
                datacenter_id=ctx.request.compute.datacenter_id,
                datastore_path=str(datastore_path),
            ),
            timeout=300,
        )
    except Exception:  # noqa: BLE001 - never mask the original failure
        log.exception("Could not remove unattended media for job %s (%s)", ctx.job_id, reason)
        return False

    artifacts.update(media_removed=True, media_removed_reason=reason)
    prepare.artifacts = artifacts
    power_on = ctx.steps_by_key.get("power_on")
    booted = power_on is not None and power_on.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED)
    if prepare.status == StepStatus.SUCCEEDED and not booted:
        prepare.status = StepStatus.PENDING
        prepare.output = "Answer media removed after the job stopped; it is regenerated on retry."
    from app.audit.recorder import record_audit

    await record_audit(ctx.db).record(
        AuditAction.UNATTENDED_MEDIA_REMOVED,
        resource_type="virtual_machine",
        resource_name=ctx.vm_name,
        job_id=ctx.job_id,
        username=ctx.actor_username,
        datacenter_id=ctx.request.compute.datacenter_id,
        datacenter_name=ctx.job.datacenter_name,
        result="success",
        details={"reason": reason},
    )
    return True


async def stage_cleanup_unattended_media(ctx: JobRunContext) -> StageOutcome:
    if not _uses_unattended_media(ctx):
        return StageOutcome(
            status="NOT_APPLICABLE",
            output="No temporary unattended answer media was created for this deployment type.",
        )
    prepare = ctx.steps_by_key.get("prepare_unattended_install")
    datastore_path = ((prepare.artifacts or {}).get("datastore_path") if prepare else None)
    lines: list[str] = []
    if datastore_path:
        await ctx.vmware.remove_temporary_floppy(
            ctx.target,
            _require_vm_id(ctx),
            datacenter_id=ctx.request.compute.datacenter_id,
            datastore_path=str(datastore_path),
        )
        lines.append("Temporary unattended answer media was detached and deleted.")
    else:
        lines.append("No temporary unattended media was recorded.")
    if prepare is not None and datastore_path:
        prepare.artifacts = {**(prepare.artifacts or {}), "media_removed": True}

    # Windows Setup caches the answer file inside the guest. Remove every copy
    # and any AutoLogon residue so the plaintext password does not survive.
    status = "SUCCEEDED"
    try:
        credentials = await ctx.resolve_guest_credentials()
        result = await ctx.guest_ops.run_powershell(
            ctx.target, ctx.vm_name, credentials, ANSWER_FILE_SCRUB_SCRIPT, 120
        )
        if result.succeeded:
            lines.append("Cached answer-file copies and AutoLogon residue were removed from the guest.")
        else:
            status = "WARNING"
            lines.append(
                f"The in-guest answer-file scrub exited with code {result.exit_code}; "
                "remove C:\\Windows\\Panther\\unattend.xml manually."
            )
    except InfraOperationError as exc:
        status = "WARNING"
        lines.append(f"The in-guest answer-file scrub could not run: {exc.human_message}")
    return StageOutcome(status=status, output="\n".join(lines))


async def assert_static_address_unclaimed(ctx: JobRunContext, address: str, prefix: int) -> None:
    """Re-check the address right before it is applied.

    Submission already ran the conflict providers and reserved the address in
    the database, but a device can appear while the job waits in the queue.
    """
    from app.services.network.conflict import (
        IcmpPingProvider,
        VMwareInventoryProvider,
        run_conflict_check,
    )

    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    if info is not None and address in (info.ip_addresses or []):
        return  # this VM already holds the address (retry after it was applied)
    report = await run_conflict_check(
        address,
        prefix,
        [IcmpPingProvider(), VMwareInventoryProvider(ctx.vmware, ctx.target, exclude_vm_name=ctx.vm_name)],
    )
    if report.conflict_detected:
        conflicts = [p.detail for p in report.providers if p.status.value == "CONFLICT_DETECTED"]
        raise InfraOperationError(
            f"The address {address} is already in use on the network.",
            reason=" ".join(conflicts) or "A conflict provider reported the address as in use.",
            recommended_action=(
                "Free the address (or choose another one and resubmit), then retry the network stage."
            ),
            technical_detail=json.dumps([p.model_dump(mode="json") for p in report.providers]),
            retryable=True,
        )


async def stage_configure_guest_network(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Guest network configuration")
    if skipped:
        return skipped
    ctx.job.guest_provisioning_status = GuestProvisioningStatus.IN_PROGRESS.value
    credentials = await ctx.resolve_guest_credentials()
    net = ctx.request.network
    if net.mode == IpMode.STATIC and net.ipv4 is not None:
        ipv4 = net.ipv4
        await assert_static_address_unclaimed(ctx, ipv4.address, ipv4.prefix)
        script = build_static_ip_script(ipv4.address, ipv4.prefix, ipv4.gateway, ipv4.dns_servers)
        configured = {
            "mode": "STATIC",
            "address": ipv4.address,
            "prefix": ipv4.prefix,
            "subnet_mask": ipv4.subnet_mask,
            "gateway": ipv4.gateway,
            "dns_servers": ipv4.dns_servers,
        }
        human = (
            f"IP configured:\n{ipv4.address}/{ipv4.prefix}\n\nGateway:\n{ipv4.gateway}\n\nDNS:\n"
            + "\n".join(ipv4.dns_servers)
        )
    else:
        script = build_dhcp_script()
        configured = {"mode": "DHCP"}
        human = "DHCP enabled on the guest adapter."

    timeout = await effective_timeout_seconds(ctx, "configure_guest_network")
    result = await ctx.guest_ops.run_powershell(
        ctx.target, ctx.vm_name, credentials, script, timeout,
    )
    if not result.succeeded:
        raise InfraOperationError(
            "The VM was created successfully, but Windows networking could not be configured.",
            reason=f"The configuration command exited with code {result.exit_code}.",
            recommended_action="Verify VMware Tools status and retry the Network Configuration stage.",
            technical_detail=(result.stdout[-1500:] + result.stderr[-500:]) or "no output captured",
            retryable=True,
        )
    return StageOutcome(output=human, artifacts=configured)


async def stage_validate_network(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Guest network validation")
    if skipped:
        return skipped
    credentials = await ctx.resolve_guest_credentials()
    net = ctx.request.network
    checks: list[str] = []

    if net.mode == IpMode.STATIC and net.ipv4 is not None:
        gateway = net.ipv4.gateway
        gw_script = (
            f"if (Test-Connection -ComputerName {ps_single_quote(gateway)} -Count 2 -Quiet) "
            f"{{ 'GW-REACHABLE' }} else {{ exit 1 }}"
        )
        result = await ctx.guest_ops.run_powershell(
            ctx.target, ctx.vm_name, credentials, gw_script, 120,
        )
        if not result.succeeded:
            raise InfraOperationError(
                "The configured default gateway is not reachable from the guest.",
                reason=f"Ping to {net.ipv4.gateway} failed after network configuration.",
                recommended_action=(
                    "Verify the IP/gateway values, VLAN assignment and firewall rules, "
                    "then retry Network Validation."
                ),
                technical_detail=result.stdout[-1000:],
                retryable=True,
            )
        checks.append(f"Gateway {net.ipv4.gateway} reachable")
        if net.ipv4.dns_servers and ctx.request.guest.domain_join:
            dns = net.ipv4.dns_servers[0]
            fqdn = ctx.request.guest.domain_join.domain
            dns_script = (
                f"$r = Resolve-DnsName -Name {ps_single_quote(fqdn)} -Server {ps_single_quote(dns)} "
                "-ErrorAction SilentlyContinue\nif ($r) { 'DNS-OK' } else { exit 1 }"
            )
            result = await ctx.guest_ops.run_powershell(
                ctx.target, ctx.vm_name, credentials, dns_script, 120,
            )
            if not result.succeeded:
                raise InfraOperationError(
                    "DNS resolution through the configured resolver failed.",
                    reason=f"'{fqdn}' could not be resolved via {dns}.",
                    recommended_action="Check the DNS server addresses and network routing, then retry.",
                    technical_detail=result.stdout[-1000:],
                    retryable=True,
                )
            checks.append(f"DNS lookup via {dns} working")
        elif net.ipv4.dns_servers:
            checks.append("DNS servers configured; name-resolution probe skipped (no domain supplied)")
    else:
        result = await ctx.guest_ops.run_powershell(
            ctx.target, ctx.vm_name, credentials, build_dhcp_probe_script(), 180,
        )
        if not result.succeeded:
            raise InfraOperationError(
                "The guest did not receive a DHCP address.",
                reason="No usable IPv4 address was found on the adapter.",
                recommended_action="Verify the port group provides DHCP connectivity, then retry.",
                technical_detail=result.stdout[-800:],
                retryable=True,
            )
        checks.append(result.stdout.strip().splitlines()[-1] if result.stdout else "DHCP lease acquired")

    return StageOutcome(output="Network validation:\n" + "\n".join(f"✓ {c}" for c in checks))


async def stage_configure_hostname(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Hostname configuration")
    if skipped:
        return skipped
    # Rename-Computer accepts only the short Windows computer name. The DNS
    # suffix is established by Add-Computer during the following domain join.
    desired = ctx.request.effective_computer_name.upper()
    identity_artifacts = {"hostname": desired, "computer_name": desired}
    if ctx.request.effective_fqdn:
        identity_artifacts["requested_fqdn"] = ctx.request.effective_fqdn
    if ctx.request.guest.domain_join is not None:
        return StageOutcome(
            status="SKIPPED",
            output=(
                f"Windows computer name '{desired}' will be applied atomically "
                "with the Active Directory join."
            ),
            artifacts=identity_artifacts,
        )

    credentials = await ctx.resolve_guest_credentials()

    probe = await ctx.guest_ops.run_powershell(
        ctx.target, ctx.vm_name, credentials, "$env:COMPUTERNAME", 60,
    )
    current = (probe.stdout or "").strip().upper().splitlines()[-1] if probe.stdout else ""
    if probe.succeeded and current == desired:
        return StageOutcome(
            status="SKIPPED",
            output=f"Windows computer name already set to '{desired}'.",
            artifacts=identity_artifacts,
        )

    result = await ctx.guest_ops.run_powershell(
        ctx.target, ctx.vm_name, credentials, build_rename_script(desired), 180,
    )
    if not result.succeeded:
        raise InfraOperationError(
            f"The Windows computer name could not be changed to '{desired}'.",
            reason=f"Rename-Computer exited with code {result.exit_code}.",
            recommended_action="Retry the hostname stage; verify local administrator permissions.",
            technical_detail=result.stdout[-1000:],
            retryable=True,
        )
    ctx.hostname_changed = True
    return StageOutcome(
        output=f"Windows computer name changed '{current or '?'}' → '{desired}' (applies at reboot).",
        artifacts=identity_artifacts,
    )


async def stage_join_domain(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Domain join")
    if skipped:
        return skipped
    join = ctx.request.guest.domain_join
    if join is None:
        return StageOutcome(status="SKIPPED", output="Domain join not requested.")
    credentials = await ctx.resolve_guest_credentials()
    desired_name = ctx.request.effective_computer_name.upper()
    observed = await probe_windows_identity(
        ctx,
        credentials,
        operation="attempting an Active Directory join",
    )
    inconsistent_active_name = bool(
        observed.active_name
        and observed.name.casefold() != observed.active_name.casefold()
    )
    if observed.has_pending_rename or observed.pending_domain_join or inconsistent_active_name:
        raise InfraOperationError(
            "A pending Windows identity change must be resolved before domain join.",
            reason=windows_identity_detail(observed),
            recommended_action=(
                "Restart the guest, confirm its active computer name and domain, then retry the "
                "domain-join stage. "
                "InfraOps did not make another Active Directory change."
            ),
            retryable=True,
        )
    if windows_identity_matches(
        observed,
        computer_name=desired_name,
        domain=join.domain,
    ):
        ctx.reboot_required = False
        return StageOutcome(
            status="SKIPPED",
            output=(
                f"Windows already reports the verified domain identity '{observed.fqdn}'; "
                "no Active Directory change was made."
            ),
            artifacts={
                "computer_name": observed.name.upper(),
                "domain": observed.domain.lower(),
                "fqdn": observed.fqdn,
                "identity_verified": True,
                "joined": True,
                "reboot_required": False,
            },
        )
    if observed.part_of_domain:
        raise InfraOperationError(
            "The guest is already joined with a different Windows domain identity.",
            reason=windows_identity_detail(observed),
            recommended_action=(
                "Resolve the existing domain membership manually before retrying. "
                "InfraOps will not move or rename an unexpected domain member automatically."
            ),
            retryable=False,
        )

    base = join.credential_secret_ref
    username = await ctx.secrets.get_secret(f"{base}/username")
    password = await ctx.secrets.get_secret(f"{base}/password")

    # A first-boot answer file has usually set the name already.
    rename_to = None if observed.name.casefold() == desired_name.casefold() else desired_name
    script = build_domain_join_script(join.domain, username, join.ou, rename_to)
    timeout = await effective_timeout_seconds(ctx, "join_domain")
    result = await ctx.guest_ops.run_powershell(
        ctx.target,
        ctx.vm_name,
        credentials,
        script,
        timeout,
        secrets={DOMAIN_JOIN_PASSWORD_SECRET: password},
    )
    if not result.succeeded:
        raise InfraOperationError(
            f"The domain join to '{join.domain}' failed.",
            reason=f"Add-Computer exited with code {result.exit_code}.",
            recommended_action=(
                "Verify the domain-join account, OU path and network path to a domain "
                "controller, then retry the domain join stage."
            ),
            technical_detail=(result.stdout + "\n" + result.stderr)[-1500:].replace(
                password, "[REDACTED]"
            ),
            retryable=True,
        )
    ctx.reboot_required = True
    return StageOutcome(
        output=(
            f"Active Directory accepted Windows computer name '{desired_name}' "
            f"for domain '{join.domain}'"
            + (f" (OU: {join.ou})" if join.ou else "")
            + "; reboot is required before the resulting DNS identity can be verified."
        ),
        artifacts={
            "computer_name": desired_name,
            "domain": join.domain,
            "identity_verified": False,
            "joined": True,
            "reboot_required": True,
            "requested_fqdn": ctx.request.effective_fqdn,
        },
    )


async def stage_reboot_guest(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Guest restart")
    if skipped:
        return skipped
    steps = ctx.steps_by_key
    join_step = steps.get("join_domain")
    join_artifacts = (join_step.artifacts or {}) if join_step else {}
    joined_requires_reboot = bool(
        join_artifacts.get("joined") and join_artifacts.get("reboot_required", True)
    )
    if not (ctx.hostname_changed or joined_requires_reboot):
        return StageOutcome(status="SKIPPED", output="No pending changes require a reboot.")

    credentials = await ctx.resolve_guest_credentials()
    # A delayed restart lets the command return cleanly before the guest
    # (and VMware Tools) goes down.
    result = await ctx.guest_ops.run_program(
        ctx.target, ctx.vm_name, credentials, SHUTDOWN_PATH,
        '/r /t 10 /d p:4:1 /c "InfraOps provisioning restart"', 60,
    )
    if not result.succeeded:
        raise InfraOperationError(
            "The guest restart could not be scheduled.",
            reason=f"shutdown.exe exited with code {result.exit_code}.",
            recommended_action="Restart the VM from the console, then retry this stage.",
            technical_detail=result.stdout[-1000:],
            retryable=True,
        )
    await asyncio.sleep(20)
    return StageOutcome(output="Guest restart initiated.")


async def stage_wait_guest_ready(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Guest availability check")
    if skipped:
        return skipped
    steps = ctx.steps_by_key
    join_step = steps.get("join_domain")
    joined = bool((join_step.artifacts or {}).get("joined")) if join_step else False
    reboot_step = steps.get("reboot_guest")
    rebooted = reboot_step is not None and reboot_step.status.value == "SUCCEEDED"
    if not (joined or rebooted):
        return StageOutcome(status="SKIPPED", output="Guest was not restarted — availability confirmed earlier.")

    credentials = await ctx.resolve_guest_credentials()
    deadline = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=600)
    attempts = 0
    last_error = ""
    while dt.datetime.now(dt.UTC) < deadline:
        attempts += 1
        try:
            probe = await ctx.guest_ops.run_program(
                ctx.target, ctx.vm_name, credentials, HOSTNAME_PATH, "", 60,
            )
            if probe.succeeded:
                return StageOutcome(output=f"Guest responsive after restart ({attempts} probe(s)).")
            last_error = f"exit code {probe.exit_code}"
        except InfraOperationError as exc:
            # Expected while the guest is restarting and Tools is unavailable.
            last_error = exc.human_message
        await asyncio.sleep(10)
    raise InfraOperationError(
        "The guest did not become responsive after the scheduled restart.",
        reason=f"Guest operations probes timed out after 10 minutes (last: {last_error or 'n/a'}).",
        recommended_action="Check the VM console in vCenter, then retry the availability stage.",
        retryable=True,
    )


async def _deploy_certificate_group(
    ctx: JobRunContext, certificate_type: str, label: str
) -> StageOutcome:
    certs = await _load_certificates(ctx.db, list(ctx.request.certificate_package_ids), certificate_type)
    if not certs:
        return StageOutcome(status="SKIPPED", output=f"No {label} certificates selected.")
    credentials = await ctx.resolve_guest_credentials()
    deployer = CertificateDeployer(ctx.guest_ops)
    records = await deployer.deploy(ctx.target, ctx.vm_name, credentials, certs)

    failures = [r for r in records if r.action == "FAILED"]
    installed = [r for r in records if r.action == "INSTALLED"]
    from app.audit.recorder import record_audit

    for record in installed:
        await record_audit(ctx.db).record(
            AuditAction.CERTIFICATE_INSTALLED,
            resource_type="certificate",
            resource_name=record.friendly_name,
            job_id=ctx.job_id,
            username=ctx.actor_username,
            datacenter_id=ctx.request.compute.datacenter_id,
            datacenter_name=ctx.job.datacenter_name,
            result="success",
            details={"store": f"LocalMachine\\{record.store}", "thumbprint": record.thumbprint},
        )

    lines = [f"{'✓' if r.verified else '✗'} {r.friendly_name} → LocalMachine\\{r.store} ({r.action})"
             for r in records]
    if failures:
        raise InfraOperationError(
            f"{len(failures)} {label} certificate(s) failed to install.",
            reason="; ".join(f"{r.friendly_name}: {r.detail}" for r in failures)[:800],
            recommended_action="Retry the certificate installation stage after reviewing the errors.",
            technical_detail=json.dumps([asdict(r) for r in records], default=str),
            retryable=True,
        )
    return StageOutcome(
        output="\n".join(lines),
        artifacts={"records": [asdict(r) for r in records]},
    )


async def stage_install_root_certificates(ctx: JobRunContext) -> StageOutcome:
    return await _deploy_certificate_group(ctx, "ROOT", "trusted root")


async def stage_install_intermediate_certificates(ctx: JobRunContext) -> StageOutcome:
    return await _deploy_certificate_group(ctx, "INTERMEDIATE", "intermediate")


async def stage_validate_certificates(ctx: JobRunContext) -> StageOutcome:
    all_certs = (
        await _load_certificates(ctx.db, list(ctx.request.certificate_package_ids), "ROOT")
        + await _load_certificates(ctx.db, list(ctx.request.certificate_package_ids), "INTERMEDIATE")
    )
    if not all_certs:
        return StageOutcome(status="SKIPPED", output="No certificates selected.")
    credentials = await ctx.resolve_guest_credentials()
    deployer = CertificateDeployer(ctx.guest_ops)
    results = await deployer.verify_all(ctx.target, ctx.vm_name, credentials, all_certs)
    failed = [r for r in results if not r.verified]
    if failed:
        raise InfraOperationError(
            f"{len(failed)} certificate(s) failed post-install verification.",
            reason="; ".join(f"{r.friendly_name}: {r.detail}" for r in failed)[:800],
            recommended_action="Retry certificate installation and verification.",
            technical_detail=json.dumps([asdict(r) for r in results], default=str),
            retryable=True,
        )
    return StageOutcome(
        output="\n".join(f"✓ {r.friendly_name} present in LocalMachine\\{r.store}" for r in results),
        artifacts={"verified": [r.friendly_name for r in results]},
    )


async def stage_resolve_dependencies(ctx: JobRunContext) -> StageOutcome:
    if not ctx.request.application_ids:
        return StageOutcome(status="SKIPPED", output="No applications selected.")
    catalog = await _load_application_catalog(ctx.db)
    ordered, errors = resolve_install_order(list(ctx.request.application_ids), catalog)
    if errors:
        raise InfraOperationError(
            "Application dependencies could not be resolved.",
            reason=" ".join(errors)[:800],
            recommended_action="Ask an administrator to correct the application catalog dependencies.",
            technical_detail=json.dumps(errors),
            retryable=False,
        )
    names = [node.name for node in ordered]
    return StageOutcome(
        output="Install order: " + " → ".join(names),
        artifacts={"order": [{"id": str(node.id), "name": node.name} for node in ordered]},
    )


async def stage_install_applications(ctx: JobRunContext) -> StageOutcome:
    from app.audit.recorder import record_audit
    from app.services.applications.installer import ApplicationInstaller

    dep_step = ctx.steps_by_key.get("resolve_dependencies")
    order = ((dep_step.artifacts or {}).get("order") if dep_step else None) or []
    if not order:
        return StageOutcome(status="SKIPPED", output="No applications to install.")

    credentials = await ctx.resolve_guest_credentials()
    installer = ApplicationInstaller(ctx.guest_ops)

    result = await ctx.db.execute(select(Application))
    apps_by_id = {app.id: app for app in result.scalars().all()}

    outcomes = []
    failures: list[str] = []
    any_reboot = False
    for entry in order:
        app = apps_by_id.get(uuid.UUID(entry["id"]))
        if app is None:
            failures.append(f"{entry['name']}: definition disappeared from the catalog")
            continue
        definition = _definition_from_orm(app)
        outcome = await installer.install(ctx.target, ctx.vm_name, credentials, definition)
        outcomes.append(outcome)
        symbol = {"INSTALLED": "✓", "ALREADY_INSTALLED": "✓", "REBOOT_REQUIRED": "↻"}.get(outcome.status, "✗")
        if outcome.status == "INSTALLED":
            await record_audit(ctx.db).record(
                AuditAction.APPLICATION_INSTALLED,
                resource_type="application",
                resource_name=outcome.name,
                job_id=ctx.job_id,
                username=ctx.actor_username,
                datacenter_id=ctx.request.compute.datacenter_id,
                datacenter_name=ctx.job.datacenter_name,
                result="success",
            )
        if outcome.status in ("FAILED",):
            failures.append(f"{outcome.name}: {outcome.output[:200]}")
        if outcome.reboot_required:
            any_reboot = True

    if failures:
        raise InfraOperationError(
            f"{len(failures)} application installation(s) failed.",
            reason="; ".join(failures)[:900],
            recommended_action="Retry the application installation stage — already-installed items are skipped.",
            technical_detail="\n".join(f"[{o.status}] {o.name}: {o.output[:400]}" for o in outcomes),
            retryable=True,
        )
    ctx.reboot_required = ctx.reboot_required or any_reboot
    report = "\n".join(f"{symbol} {o.name} ({o.status})" for o in outcomes)
    return StageOutcome(
        output=report,
        artifacts={
            "outcomes": [
                {"name": o.name, "status": o.status, "reboot_required": o.reboot_required}
                for o in outcomes
            ],
            "reboot_required": any_reboot,
        },
    )


async def stage_validate_applications(ctx: JobRunContext) -> StageOutcome:
    from app.services.applications.installer import ApplicationInstaller

    install_step = ctx.steps_by_key.get("install_applications")
    outcomes = ((install_step.artifacts or {}).get("outcomes") if install_step else None) or []
    if not outcomes:
        return StageOutcome(status="SKIPPED", output="No applications were installed.")

    credentials = await ctx.resolve_guest_credentials()
    installer = ApplicationInstaller(ctx.guest_ops)
    result = await ctx.db.execute(select(Application))
    apps_by_name = {app.name: app for app in result.scalars().all()}

    lines, problems = [], []
    for entry in outcomes:
        app = apps_by_name.get(entry["name"])
        if app is None:
            problems.append(f"{entry['name']}: definition missing during validation")
            continue
        detected = await installer.detect(
            ctx.target, ctx.vm_name, credentials, _definition_from_orm(app)
        )
        if detected:
            lines.append(f"✓ {entry['name']}")
        else:
            lines.append(f"✗ {entry['name']} not detected after installation")
            problems.append(entry["name"])

    if problems:
        raise InfraOperationError(
            f"Post-install detection failed for: {', '.join(problems)}.",
            reason="Detection rules did not match after installation.",
            recommended_action="Review the detection rules with an administrator and retry validation.",
            technical_detail=json.dumps(problems),
            retryable=True,
        )
    return StageOutcome(output="\n".join(lines), artifacts={"validated": [entry["name"] for entry in outcomes]})


async def stage_final_validation(ctx: JobRunContext) -> StageOutcome:
    automates_guest = not (
        ctx.request.source_type == VmSourceType.BLANK
        and getattr(getattr(ctx.request, "guest", None), "iso_id", None) is None
    )
    checklist: list[dict] = []
    observed_computer_name = (
        ctx.request.effective_computer_name
        if (
            automates_guest
            and ctx.request.guest.domain_join is None
        )
        else None
    )
    observed_fqdn = None

    def add(group: str, label: str, ok: bool, detail: str = "") -> None:
        checklist.append({"group": group, "label": label, "status": "PASS" if ok else "FAIL",
                          "detail": detail})

    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    add("VM", "Exists in vCenter", info is not None)
    if info is not None:
        if not automates_guest:
            add("VM", "Left powered off for OS installation", info.power_state == "poweredOff",
                info.power_state)
        else:
            add("VM", "Powered on", info.power_state == "poweredOn", info.power_state)
            add("VM", "VMware Tools running", info.tools_status in ("toolsOk", "toolsOld"),
                info.tools_status or "unknown")

    net_step = ctx.steps_by_key.get("configure_guest_network")
    net_artifacts = net_step.artifacts if net_step else {}
    if not automates_guest:
        inventory_step = ctx.steps_by_key.get("validate_infrastructure")
        inventory_artifacts = inventory_step.artifacts if inventory_step else {}
        add(
            "Network",
            "Virtual adapter attached",
            True,
            str(inventory_artifacts.get("network_name") or ""),
        )
    elif ctx.request.network.mode == IpMode.STATIC and ctx.request.network.ipv4 is not None:
        expected = ctx.request.network.ipv4.address
        observed = info.ip_addresses if info else []
        add("Network", f"Correct IP address ({expected})", expected in observed,
            ", ".join(observed) or "none reported")
        add("Network", "Gateway reachable", True,
            str(net_artifacts.get("gateway", "")))
        add("Network", "DNS servers configured",
            bool(net_artifacts.get("dns_servers")),
            ", ".join(net_artifacts.get("dns_servers", [])))
    else:
        add("Network", "DHCP address acquired", bool(info and info.ip_addresses),
            ", ".join(info.ip_addresses) if info else "")

    if automates_guest and ctx.request.guest.domain_join is not None:
        credentials = await ctx.resolve_guest_credentials()
        identity = await probe_windows_identity(
            ctx,
            credentials,
            operation="performing post-reboot validation",
        )
        identity_matches = windows_identity_matches(
            identity,
            computer_name=ctx.request.effective_computer_name,
            domain=ctx.request.guest.domain_join.domain,
        )
        if identity_matches:
            observed_computer_name = identity.name.upper()
            observed_fqdn = identity.fqdn
            add(
                "Identity",
                f"Observed domain identity {observed_fqdn}",
                True,
                windows_identity_detail(identity),
            )
        else:
            add(
                "Identity",
                "Observed Windows identity matches the requested computer and domain",
                False,
                windows_identity_detail(identity),
            )

    cert_step = ctx.steps_by_key.get("validate_certificates")
    verified = ((cert_step.artifacts or {}).get("verified") if cert_step else None) or []
    for name in verified:
        add("Certificates", f"{name} installed", True)

    app_step = ctx.steps_by_key.get("validate_applications")
    validated_apps = ((app_step.artifacts or {}).get("validated") if app_step else None) or []
    for name in validated_apps:
        add("Applications", name, True)

    failures = [item for item in checklist if item["status"] == "FAIL"]
    if failures:
        raise InfraOperationError(
            "Final validation reported unresolved problems.",
            reason="; ".join(f"{i['group']}/{i['label']}" for i in failures),
            recommended_action="Review the validation checklist and retry the affected stages.",
            technical_detail=json.dumps(checklist, default=str),
            retryable=True,
        )

    grouped_output: dict[str, list[str]] = {}
    for item in checklist:
        grouped_output.setdefault(item["group"], []).append(f"✓ {item['label']}")
    rendered = "\n\n".join(f"{group}\n" + "\n".join(items) for group, items in grouped_output.items())
    if hasattr(ctx, "job"):
        ctx.job.infrastructure_status = InfrastructureStatus.READY.value
        ctx.job.guest_provisioning_status = (
            GuestProvisioningStatus.COMPLETED.value
            if automates_guest
            else GuestProvisioningStatus.NOT_REQUESTED.value
        )
    return StageOutcome(
        output=rendered,
        artifacts={
            "checklist": checklist,
            "summary": {
                "vm_name": ctx.vm_name,
                "computer_name": observed_computer_name,
                "fqdn": observed_fqdn,
                "ip_address": (info.ip_addresses[0] if info and info.ip_addresses else None),
                "finished_at": dt.datetime.now(dt.UTC).isoformat(),
            },
        },
    )


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


STAGE_HANDLERS: dict[str, StageHandler] = {
    stage.key: handler
    for stage, handler in zip(ORDERED_STAGES, (
        stage_validate_request,
        stage_connect_vcenter,
        stage_validate_infrastructure,
        stage_clone_vm,
        stage_configure_hardware,
        stage_attach_network_adapter,
        stage_prepare_unattended_install,
        stage_power_on,
        stage_wait_for_guest_os,
        stage_wait_for_tools,
        stage_cleanup_unattended_media,
        stage_configure_guest_network,
        stage_validate_network,
        stage_configure_hostname,
        stage_join_domain,
        stage_reboot_guest,
        stage_wait_guest_ready,
        stage_install_root_certificates,
        stage_install_intermediate_certificates,
        stage_validate_certificates,
        stage_resolve_dependencies,
        stage_install_applications,
        stage_validate_applications,
        stage_final_validation,
    ), strict=False)
}
