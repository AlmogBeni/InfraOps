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
import secrets
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
from app.schemas.provisioning import DESKTOP_EXPERIENCE_EDITIONS, IpMode
from app.services.applications.installer import ApplicationDefinition
from app.services.applications.paths import path_within_roots
from app.services.applications.resolver import AppNode, resolve_install_order
from app.services.certificates.deployer import CertificateDeployer, CertificateToDeploy
from app.services.guest.scripts import HOSTNAME_PATH
from app.services.guest.scripts import ps_quote as ps_single_quote
from app.services.settings_store import (
    SETTING_ALLOWED_INSTALLER_ROOTS,
    SETTING_VM_NAME_POLICY,
    load_effective,
)
from app.services.vmware.base import PowerStateInfo, VmRef
from app.services.windows_unattend import (
    WindowsUnattendSpec,
    build_answer_iso,
    build_autounattend_xml,
)
from app.workers.context import JobRunContext

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


DATA_DISKS_NOT_VISIBLE_EXIT = 3


def build_initialize_data_disks_script(expected_count: int) -> str:
    """Bring hot-added data disks online and format them (idempotent).

    Every disk except the boot/system disk is a data disk. Windows' SAN policy
    can leave new disks offline or read-only, so both are cleared first. A
    disk that is already partitioned and formatted is left untouched, so a
    retry never reformats anything. Exits DATA_DISKS_NOT_VISIBLE_EXIT while
    Windows has not detected all hot-added disks yet.
    """
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "Update-HostStorageCache\n"
        "$data = @(Get-Disk | Where-Object { -not ($_.IsBoot -or $_.IsSystem) } | Sort-Object Number)\n"
        f"if ($data.Count -lt {int(expected_count)}) {{ \"FOUND:$($data.Count)\"; "
        f"exit {DATA_DISKS_NOT_VISIBLE_EXIT} }}\n"
        "$index = 0\n"
        "$volumes = foreach ($disk in $data) {\n"
        "    $index++\n"
        "    if ($disk.IsOffline) { Set-Disk -Number $disk.Number -IsOffline $false }\n"
        "    if ($disk.IsReadOnly) { Set-Disk -Number $disk.Number -IsReadOnly $false }\n"
        "    $disk = Get-Disk -Number $disk.Number\n"
        "    if ($disk.PartitionStyle -eq 'RAW') { Initialize-Disk -Number $disk.Number -PartitionStyle GPT }\n"
        "    $partition = Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue |\n"
        "        Where-Object { $_.Type -eq 'Basic' } | Select-Object -First 1\n"
        "    if (-not $partition) {\n"
        "        $partition = New-Partition -DiskNumber $disk.Number -UseMaximumSize -AssignDriveLetter\n"
        "    }\n"
        "    $volume = $partition | Get-Volume\n"
        "    if (-not $volume.FileSystem) {\n"
        "        $volume = Format-Volume -Partition $partition -FileSystem NTFS "
        "-NewFileSystemLabel ('Data' + $index) -Confirm:$false\n"
        "    }\n"
        "    [pscustomobject]@{Number=[int]$disk.Number; SizeGB=[int][math]::Round($disk.Size / 1GB);"
        " DriveLetter=[string]$partition.DriveLetter; Label=[string]$volume.FileSystemLabel;"
        " FileSystem=[string]$volume.FileSystem}\n"
        "}\n"
        "ConvertTo-Json -Compress -InputObject @($volumes)"
    )


@dataclass(frozen=True)
class DataVolume:
    number: int
    size_gb: int
    drive_letter: str
    label: str
    file_system: str


def parse_data_volumes(stdout: str) -> list[DataVolume]:
    for line in reversed([entry.strip() for entry in stdout.splitlines() if entry.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        if all(isinstance(item, dict) and "Number" in item for item in items):
            return [
                DataVolume(
                    number=int(item["Number"]),
                    size_gb=int(item.get("SizeGB") or 0),
                    drive_letter=str(item.get("DriveLetter") or ""),
                    label=str(item.get("Label") or ""),
                    file_system=str(item.get("FileSystem") or ""),
                )
                for item in items
            ]
    raise ValueError("The data-disk script did not return a JSON list.")


# ── helpers ──────────────────────────────────────────────────────────────────

_TIMEOUT_KEY_MAP: dict[str, tuple[str, ...]] = {
    "create_vm": ("create_vm_minutes",),
    "wait_for_guest_os": ("os_installation_minutes",),
    "wait_for_tools": ("vmware_tools_minutes",),
    "configure_guest_network": ("network_configuration_minutes",),
    "validate_network": ("network_configuration_minutes",),
    "initialize_data_disks": ("guest_operations_minutes",),
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
    for setting_key in _TIMEOUT_KEY_MAP.get(stage_key, ()):
        if setting_key in override:
            return float(override[setting_key]) * 60
    return float(stage_timeout(stage_key, DEFAULT_STAGE_TIMEOUTS))


def _require_vm_id(ctx: JobRunContext) -> str:
    if ctx.vm_ref is None:
        step = ctx.steps_by_key.get("create_vm")
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


def _os_disk_datastore(ctx: JobRunContext) -> str | None:
    disks = ctx.request.hardware.disks
    return disks[0].datastore_id or next((d.datastore_id for d in disks if d.datastore_id), None)


# ── stage handlers ───────────────────────────────────────────────────────────

async def stage_validate_request(ctx: JobRunContext) -> StageOutcome:
    ctx.job.infrastructure_status = InfrastructureStatus.PENDING.value
    ctx.job.guest_os_status = GuestOsStatus.UNKNOWN.value
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
            f"VM: {r.vm.name}\nInfrastructure placement supplied.\n"
            f"CPU/Memory: {r.hardware.cpu} vCPU / {r.hardware.memory_mb} MB\n"
            f"Disks: {len(r.hardware.disks)} (OS disk + {len(r.hardware.disks) - 1} data)\n"
            f"Network: {r.network.mode.value}"
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

    isos = {
        image.id: image
        for image in (
            await ctx.vmware.get_isos(ctx.target, r.compute.datacenter_id) if dc is not None else []
        )
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
    return StageOutcome(
        output="Datacenter, cluster, placement, storage, network and installation ISO verified.",
        artifacts={
            "datacenter_name": dc.name if dc is not None else None,
            "network_name": network.name if network is not None else None,
            "iso_name": image.name if image is not None else None,
        },
    )


async def stage_create_vm(ctx: JobRunContext) -> StageOutcome:
    from app.audit.recorder import record_audit
    from app.services.vmware.base import VmCreateSpec

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

    os_disk = r.hardware.disks[0]
    spec = VmCreateSpec(
        vm_name=r.vm.name,
        datacenter_id=r.compute.datacenter_id,
        iso_id=r.guest.iso_id,
        os_disk=os_disk,
        description=r.vm.description,
        cluster_id=r.compute.cluster_id,
        host_id=r.compute.host_id,
        resource_pool_id=r.compute.resource_pool_id,
        datastore_id=_os_disk_datastore(ctx),
        cpu=r.hardware.cpu,
        memory_mb=r.hardware.memory_mb,
        firmware=r.hardware.firmware,
        secure_boot=r.hardware.secure_boot,
        job_id=job_marker,
    )
    vm_ref = await ctx.vmware.create_vm(ctx.target, spec)
    ctx.vm_ref = vm_ref
    ctx.job.infrastructure_status = InfrastructureStatus.READY.value
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
            "iso_id": r.guest.iso_id,
            "vm_id": vm_ref.id,
        },
    )
    data_disks = len(r.hardware.disks) - 1
    return StageOutcome(
        output=(
            f"Created '{vm_ref.name}' powered off with its {os_disk.size_gb} GB OS disk, the Windows "
            "installation ISO and the host's VMware Tools ISO."
            + (f" {data_disks} data disk(s) are added after Windows is installed." if data_disks else "")
        ),
        artifacts={"vm_id": vm_ref.id, "vm_name": vm_ref.name, "owner_job_id": job_marker},
    )


async def stage_configure_hardware(ctx: JobRunContext) -> StageOutcome:
    vm_id = _require_vm_id(ctx)
    hw = ctx.request.hardware
    await ctx.vmware.configure_hardware(ctx.target, vm_id, cpu=hw.cpu, memory_mb=hw.memory_mb)
    return StageOutcome(
        output=(
            f"CPU: {hw.cpu} vCPU\nMemory: {hw.memory_mb} MB\nFirmware: {hw.firmware.value}"
            f"{' + Secure Boot' if hw.secure_boot else ''}"
        ),
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


async def stage_prepare_unattended_install(ctx: JobRunContext) -> StageOutcome:
    vm_id = _require_vm_id(ctx)
    guest = ctx.request.guest
    credentials = await ctx.resolve_guest_credentials()
    product_key = None
    if guest.product_key_secret_ref:
        product_key = (await ctx.secrets.get_secret(f"{guest.product_key_secret_ref}/password")).strip()
    try:
        xml = build_autounattend_xml(
            WindowsUnattendSpec(
                computer_name=ctx.request.effective_computer_name,
                administrator_username=credentials.username,
                administrator_password=credentials.password,
                image_index=guest.windows_image_index,
                locale=guest.installation_locale,
                input_locale=guest.input_locale,
                timezone=guest.timezone or "UTC",
                firmware=ctx.request.hardware.firmware.value,
                product_key=product_key,
                builtin_administrator_password=f"{secrets.token_urlsafe(24)}aA1!",
            )
        )
    except ValueError as exc:
        raise InfraOperationError(
            "The answer file could not be generated from the selected credentials.",
            reason=str(exc),
            recommended_action=(
                "Select a provisioning credential whose username is a local account such as "
                "'Administrator' and, for a product key, a valid 25-character key; then submit a "
                "new request."
            ),
            retryable=False,
        ) from exc
    # The media contains a plaintext Setup password (and product key) by
    # necessity. It is held only in memory here, uploaded directly, and removed
    # once Windows is installed — or as soon as the job stops earlier.
    ref = await ctx.vmware.attach_answer_media(
        ctx.target,
        vm_id,
        datacenter_id=ctx.request.compute.datacenter_id,
        datastore_id=_os_disk_datastore(ctx),
        file_name=f"infraops-{ctx.job_id}.iso",
        content=build_answer_iso(xml),
    )
    return StageOutcome(
        output=(
            "Temporary answer media attached as a CD drive. Windows Setup installs the selected "
            "image to disk 0 and configures the administrator account, locale, keyboard, time zone "
            "and computer name without showing any page"
            + (", using the selected product key." if product_key else ".")
        ),
        artifacts={"datastore_path": ref.datastore_path},
    )


# USB HID usage ID of the space bar.
_HID_SPACEBAR = 0x2C
# Windows installation media boots only after "Press any key to boot from CD or
# DVD", shown for about five seconds once the firmware reaches the CD; when it is
# missed the firmware retries the CD every 10 seconds. A press every 3 seconds
# always lands inside the prompt, and 45 seconds cover slow firmware plus a
# retry. vCenter records every press as a task, so presses are kept this sparse.
# Setup's first restart, after which a key press would boot the CD again, is
# many minutes later.
_BOOT_KEY_SECONDS = 45.0
_BOOT_KEY_INTERVAL_SECONDS = 3.0


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
    vm_id = _require_vm_id(ctx)
    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    already_on = info is not None and info.power_state == "poweredOn"
    # This stage reruns only after it failed, so Setup cannot have started:
    # restart the firmware to get the boot prompt back.
    if already_on:
        await ctx.vmware.reset(ctx.target, vm_id)
    else:
        await ctx.vmware.power_on(ctx.target, vm_id)
    sent = await press_key_to_boot_from_iso(ctx, vm_id)
    ctx.job.guest_os_status = GuestOsStatus.INSTALLATION_IN_PROGRESS.value
    return StageOutcome(
        output=(
            f"{'Reset' if already_on else 'Power-on'} task completed. The space bar was pressed "
            f"{sent} time(s) during the first {int(_BOOT_KEY_SECONDS)} seconds to answer the "
            "installation media's 'Press any key to boot from CD or DVD' prompt."
        ),
        artifacts={"boot_keys_sent": sent},
    )


async def stage_wait_for_guest_os(ctx: JobRunContext) -> StageOutcome:
    """Windows installs from the ISO, then — at the first logon, which happens only
    after Setup and OOBE have finished — installs VMware Tools from the host's
    Tools ISO. Windows Server media carry no VMware Tools, so a running Tools
    service with Guest Operations ready proves the installation finished. Power
    state is never accepted as proof, and no file transfer is needed here."""
    vm_id = _require_vm_id(ctx)
    ctx.job.guest_os_status = GuestOsStatus.INSTALLATION_IN_PROGRESS.value
    ctx.job.vmware_tools_status = VMwareToolsStatus.NOT_APPLICABLE_YET.value
    ctx.job.guest_provisioning_status = GuestProvisioningStatus.WAITING_FOR_OS.value
    timeout = await effective_timeout_seconds(ctx, "wait_for_guest_os")
    try:
        # Finish with a clear error before the pipeline's own stage timeout fires.
        await ctx.vmware.wait_for_tools(ctx.target, vm_id, max(60.0, timeout - 60.0))
    except InfraOperationError as exc:
        raise InfraOperationError(
            "Windows did not finish installing from the ISO in time.",
            reason=(
                "VMware Tools never reported from the new installation, so Windows Setup or the "
                "first-logon VMware Tools installation did not complete."
            ),
            recommended_action=(
                "Check the console screenshot in the administrator diagnostics: an EFI boot list or "
                "'Press any key to boot from CD or DVD' means Setup never started; a Windows Setup "
                "page or error usually points at the edition, product key or ISO; a Windows desktop "
                "without VMware Tools means the host's Tools ISO "
                "([] /vmimages/tools-isoimages/windows.iso) was not available. Fix the cause and "
                "submit a new request."
            ),
            technical_detail=exc.technical_detail,
            retryable=True,
        ) from exc
    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    if info is not None and info.guest_family and "windows" not in info.guest_family.casefold():
        raise InfraOperationError(
            f"The installed guest reports '{info.guest_family}', not Windows.",
            reason="Only Windows Server installation media is supported.",
            recommended_action="Select a Windows Server ISO and submit a new request.",
            retryable=False,
        )
    ctx.job.guest_os_status = GuestOsStatus.READY.value
    ctx.job.guest_provisioning_status = GuestProvisioningStatus.IN_PROGRESS.value
    computer_name = info.guest_host_name if info is not None else None
    return StageOutcome(
        output=(
            "Windows was installed unattended from the ISO. VMware Tools, which Windows installs "
            "at the first logon after Setup and OOBE, is running"
            + (f" and reports the computer name '{computer_name}'." if computer_name else ".")
        ),
        artifacts={"windows_setup": {"computer_name": computer_name}},
    )


async def stage_wait_for_tools(ctx: JobRunContext) -> StageOutcome:
    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    state = _tools_lifecycle(info)
    ctx.job.vmware_tools_status = state.value
    if state == VMwareToolsStatus.RUNNING:
        return StageOutcome(output="VMware Tools is installed, current, and running.")
    if state == VMwareToolsStatus.OUTDATED:
        return StageOutcome(
            status="WARNING",
            output=(
                "VMware Tools is running but outdated (the host's Tools ISO is older than the "
                "latest release). Provisioning continued; upgrade Tools during regular patching."
            ),
        )
    raise InfraOperationError(
        "VMware Tools stopped reporting after Windows was installed.",
        reason=f"VMware Tools state: {state.value}.",
        recommended_action=(
            "Check the console screenshot in the administrator diagnostics, then retry this stage; "
            "InfraOps does not reinstall Tools over a running installation."
        ),
        retryable=True,
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
    "foreach ($name in 'DefaultPassword', 'AutoLogonCount') {\n"
    "    Remove-ItemProperty -Path $winlogon -Name $name -ErrorAction SilentlyContinue\n"
    "}\n"
    "Set-ItemProperty -Path $winlogon -Name AutoAdminLogon -Value '0' -ErrorAction SilentlyContinue\n"
    "'ANSWER-FILE-SCRUBBED'"
)


async def release_unattended_media(ctx: JobRunContext, *, reason: str) -> bool:
    """Detach and delete the answer media on a failure/cancel/interrupt path.

    The media holds the local administrator password in plain text, so it
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
            ctx.vmware.remove_answer_media(
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
    vm_id = _require_vm_id(ctx)
    prepare = ctx.steps_by_key.get("prepare_unattended_install")
    datastore_path = ((prepare.artifacts or {}).get("datastore_path") if prepare else None)
    lines: list[str] = []
    status = "SUCCEEDED"
    if datastore_path:
        await ctx.vmware.remove_answer_media(
            ctx.target,
            vm_id,
            datacenter_id=ctx.request.compute.datacenter_id,
            datastore_path=str(datastore_path),
        )
        prepare.artifacts = {**(prepare.artifacts or {}), "media_removed": True}
        lines.append("Temporary answer media was detached and deleted.")
    else:
        lines.append("No temporary answer media was recorded.")

    # The installation and Tools ISOs are no longer needed; the VM now boots
    # from its disk only, so later restarts never reach the DVD prompt. Done
    # before the guest step below so the media is released even if that fails.
    try:
        released = await ctx.vmware.detach_installation_media(ctx.target, vm_id)
        lines.append(
            "Installation media disconnected and boot order set to disk only"
            + (f": {', '.join(released)}." if released else ".")
        )
    except InfraOperationError as exc:
        status = "WARNING"
        lines.append(f"The installation ISOs could not be disconnected: {exc.human_message}")

    # Windows Setup caches the answer file inside the guest. Remove every copy
    # and the AutoLogon residue so the plaintext password does not survive;
    # the job does not continue while a copy may remain.
    credentials = await ctx.resolve_guest_credentials()
    try:
        result = await ctx.guest_ops.run_powershell(
            ctx.target, ctx.vm_name, credentials, ANSWER_FILE_SCRUB_SCRIPT, 120
        )
    except InfraOperationError as exc:
        raise InfraOperationError(
            "The cached answer file could not be removed from Windows.",
            reason=f"{exc.human_message} {exc.reason}",
            recommended_action=exc.recommended_action,
            technical_detail=exc.technical_detail,
            retryable=True,
        ) from exc
    if not result.succeeded:
        raise InfraOperationError(
            "The cached answer file could not be removed from Windows.",
            reason=f"The in-guest cleanup exited with code {result.exit_code}.",
            recommended_action="Review the technical output, then retry this stage.",
            technical_detail=(result.stdout + "\n" + result.stderr)[-1500:],
            retryable=True,
        )
    lines.append("Cached answer-file copies and AutoLogon values were removed from the guest.")
    return StageOutcome(status=status, output="\n".join(lines))


async def stage_add_data_disks(ctx: JobRunContext) -> StageOutcome:
    data_disks = list(ctx.request.hardware.disks[1:])
    if not data_disks:
        return StageOutcome(status="SKIPPED", output="No data disks were requested.")
    added = await ctx.vmware.add_data_disks(ctx.target, _require_vm_id(ctx), data_disks)
    sizes = ", ".join(f"{disk.size_gb} GB {disk.provisioning.value}" for disk in data_disks)
    return StageOutcome(
        output=(
            f"{len(data_disks)} data disk(s) attached to the running VM ({sizes}); "
            f"{added} added now, {len(data_disks) - added} already present."
        ),
        artifacts={"data_disks": [disk.size_gb for disk in data_disks]},
    )


# Windows detects hot-added disks within seconds; allow for a slow rescan.
_DATA_DISK_DETECTION_DELAYS = (5.0, 10.0, 20.0, 30.0, 30.0)


async def stage_initialize_data_disks(ctx: JobRunContext) -> StageOutcome:
    expected = sorted(disk.size_gb for disk in ctx.request.hardware.disks[1:])
    if not expected:
        return StageOutcome(status="SKIPPED", output="No data disks were requested.")
    credentials = await ctx.resolve_guest_credentials()
    timeout = await effective_timeout_seconds(ctx, "initialize_data_disks")
    script = build_initialize_data_disks_script(len(expected))
    result = None
    for delay in (0.0, *_DATA_DISK_DETECTION_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        result = await ctx.guest_ops.run_powershell(
            ctx.target, ctx.vm_name, credentials, script, timeout
        )
        if result.exit_code != DATA_DISKS_NOT_VISIBLE_EXIT:
            break
    assert result is not None
    if result.exit_code == DATA_DISKS_NOT_VISIBLE_EXIT:
        raise InfraOperationError(
            "Windows did not detect every hot-added data disk.",
            reason=f"Expected {len(expected)} data disk(s); Windows reported {result.stdout.strip()}.",
            recommended_action="Check the VM's disks in vCenter, then retry this stage.",
            technical_detail=result.stdout[-1000:],
            retryable=True,
        )
    if not result.succeeded:
        raise InfraOperationError(
            "The data disks could not be brought online and formatted.",
            reason=f"The disk script exited with code {result.exit_code}.",
            recommended_action="Review the technical output, then retry this stage.",
            technical_detail=(result.stdout + "\n" + result.stderr)[-1500:],
            retryable=True,
        )
    try:
        volumes = parse_data_volumes(result.stdout)
    except ValueError as exc:
        raise InfraOperationError(
            "The data-disk result could not be interpreted.",
            reason=str(exc),
            recommended_action="Retry this stage.",
            technical_detail=result.stdout[-1500:],
            retryable=True,
        ) from exc
    found = sorted(volume.size_gb for volume in volumes)
    unformatted = [volume for volume in volumes if not volume.file_system]
    if found != expected or unformatted:
        raise InfraOperationError(
            "The data disks Windows reports do not match the request.",
            reason=f"Requested sizes {expected} GB; Windows reports {found} GB.",
            recommended_action="Check the VM's disks in vCenter, then retry this stage.",
            technical_detail=result.stdout[-1500:],
            retryable=True,
        )
    lines = [
        f"Disk {volume.number}: {volume.size_gb} GB {volume.file_system} "
        f"{volume.drive_letter + ':' if volume.drive_letter else '(no drive letter)'} "
        f"'{volume.label}'"
        for volume in volumes
    ]
    return StageOutcome(
        output="Data disks online and formatted:\n" + "\n".join(lines),
        artifacts={"volumes": [asdict(volume) for volume in volumes]},
    )


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


# "Server" for the Desktop Experience, "Server Core" without it.
INSTALLATION_TYPE_SCRIPT = (
    "(Get-ItemProperty -LiteralPath "
    "'Registry::HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion' "
    "-ErrorAction Stop).InstallationType"
)


async def windows_installation_type(ctx: JobRunContext) -> str:
    credentials = await ctx.resolve_guest_credentials()
    result = await ctx.guest_ops.run_powershell(
        ctx.target, ctx.vm_name, credentials, INSTALLATION_TYPE_SCRIPT, 60
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not result.succeeded or not lines:
        raise InfraOperationError(
            "Windows did not report its installation type.",
            reason=f"The read-only registry query exited with code {result.exit_code}.",
            recommended_action="Retry this stage.",
            technical_detail=(result.stdout + "\n" + result.stderr)[-1000:],
            retryable=True,
        )
    return lines[-1]


async def stage_final_validation(ctx: JobRunContext) -> StageOutcome:
    checklist: list[dict] = []
    observed_computer_name = (
        ctx.request.effective_computer_name if ctx.request.guest.domain_join is None else None
    )
    observed_fqdn = None

    def add(group: str, label: str, ok: bool, detail: str = "") -> None:
        checklist.append({"group": group, "label": label, "status": "PASS" if ok else "FAIL",
                          "detail": detail})

    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    add("VM", "Exists in vCenter", info is not None)
    if info is not None:
        add("VM", "Powered on", info.power_state == "poweredOn", info.power_state)
        add("VM", "VMware Tools running", info.tools_status in ("toolsOk", "toolsOld"),
            info.tools_status or "unknown")

    edition = DESKTOP_EXPERIENCE_EDITIONS.get(ctx.request.guest.windows_image_index)
    if edition is not None:
        installation_type = await windows_installation_type(ctx)
        add("Windows", f"{edition} installed", installation_type == "Server", installation_type)
        if installation_type != "Server":
            raise InfraOperationError(
                "Windows was installed without the Desktop Experience.",
                reason=(
                    f"Windows reports installation type '{installation_type}' for edition index "
                    f"{ctx.request.guest.windows_image_index}, which Microsoft's Windows Server media "
                    f"use for {edition}."
                ),
                recommended_action=(
                    "The ISO lists its editions in a different order than Microsoft's Windows Server "
                    "media. The Desktop Experience cannot be added to a Server Core installation: "
                    "delete the VM and submit a new request with unmodified Microsoft media."
                ),
                technical_detail=json.dumps(checklist, default=str),
                retryable=False,
            )

    disk_step = ctx.steps_by_key.get("initialize_data_disks")
    for volume in ((disk_step.artifacts or {}).get("volumes") if disk_step else None) or []:
        letter = volume.get("drive_letter")
        add(
            "Storage",
            f"{volume.get('size_gb')} GB data volume {letter + ':' if letter else ''} online",
            bool(volume.get("file_system")),
            str(volume.get("label") or ""),
        )

    net_step = ctx.steps_by_key.get("configure_guest_network")
    net_artifacts = net_step.artifacts if net_step else {}
    if ctx.request.network.mode == IpMode.STATIC and ctx.request.network.ipv4 is not None:
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

    if ctx.request.guest.domain_join is not None:
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
        ctx.job.guest_provisioning_status = GuestProvisioningStatus.COMPLETED.value
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


STAGE_HANDLERS: dict[str, StageHandler] = {
    "validate_request": stage_validate_request,
    "connect_vcenter": stage_connect_vcenter,
    "validate_infrastructure": stage_validate_infrastructure,
    "create_vm": stage_create_vm,
    "configure_hardware": stage_configure_hardware,
    "attach_network_adapter": stage_attach_network_adapter,
    "prepare_unattended_install": stage_prepare_unattended_install,
    "power_on": stage_power_on,
    "wait_for_guest_os": stage_wait_for_guest_os,
    "wait_for_tools": stage_wait_for_tools,
    "cleanup_unattended_media": stage_cleanup_unattended_media,
    "add_data_disks": stage_add_data_disks,
    "initialize_data_disks": stage_initialize_data_disks,
    "configure_guest_network": stage_configure_guest_network,
    "validate_network": stage_validate_network,
    "configure_hostname": stage_configure_hostname,
    "join_domain": stage_join_domain,
    "reboot_guest": stage_reboot_guest,
    "wait_guest_ready": stage_wait_guest_ready,
    "install_root_certificates": stage_install_root_certificates,
    "install_intermediate_certificates": stage_install_intermediate_certificates,
    "validate_certificates": stage_validate_certificates,
    "resolve_dependencies": stage_resolve_dependencies,
    "install_applications": stage_install_applications,
    "validate_applications": stage_validate_applications,
    "final_validation": stage_final_validation,
}
