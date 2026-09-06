"""Pipeline stage implementations for VM provisioning.

Each handler receives the :class:`JobRunContext`, performs exactly one
concern, and returns a :class:`StageOutcome`. Handlers raise
:class:`InfraOperationError` on failure — the pipeline converts that into the
human/technical error pair persisted on the job step.

Every command string reaching a guest is assembled exclusively from typed,
validated values (IP octets, enum stores, GUIDs, server-generated paths);
credentials are interpolated only into scripts transferred through the
encrypted VMware channel and are never logged or persisted.
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
from app.schemas.provisioning import IpMode, VmSourceType
from app.services.applications.installer import ApplicationDefinition
from app.services.applications.resolver import AppNode, resolve_install_order
from app.services.certificates.deployer import CertificateDeployer, CertificateToDeploy
from app.services.certificates.store_logic import POWERSHELL_PATH
from app.services.settings_store import (
    SETTING_ALLOWED_INSTALLER_ROOTS,
    SETTING_VM_NAME_POLICY,
    load_effective,
)
from app.services.vmware.base import VmRef
from app.services.windows_unattend import (
    WindowsUnattendSpec,
    build_autounattend_xml,
    build_unattend_iso,
)
from app.workers.context import JobRunContext
from app.workers.state_machine import ORDERED_STAGES

log = get_logger(__name__)

CMD_PATH = r"C:\Windows\System32\cmd.exe"


@dataclass
class StageOutcome:
    status: str = "SUCCEEDED"  # SUCCEEDED | SKIPPED
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

def ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_static_ip_script(address: str, prefix: int, gateway: str, dns_servers: list[str]) -> str:
    dns_list = ",".join(ps_single_quote(d) for d in dns_servers) or "''"
    return (
        "$adapter = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | Select-Object -First 1; "
        "if (-not $adapter) { throw 'No connected network adapter found' }; "
        "Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -Dhcp Disabled; "
        f"New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress {ps_single_quote(address)} "
        f"-PrefixLength {int(prefix)} -DefaultGateway {ps_single_quote(gateway)} | Out-Null; "
        f"Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses {dns_list} | Out-Null; "
        "'NETWORK-CONFIGURED'"
    )


def build_dhcp_script() -> str:
    return (
        "$adapter = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | Select-Object -First 1; "
        "if (-not $adapter) { throw 'No connected network adapter found' }; "
        "Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -Dhcp Enabled; "
        "Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ResetServerAddresses | Out-Null; "
        "'NETWORK-DHCP-ENABLED'"
    )


def build_rename_script(new_name: str) -> str:
    return f"Rename-Computer -NewName {ps_single_quote(new_name)} -Force -ErrorAction Stop | Out-Null; 'RENAMED'"


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
    result = await ctx.guest_ops.run_program(
        ctx.target,
        ctx.vm_name,
        credentials,
        POWERSHELL_PATH,
        f"-NoProfile -NonInteractive -Command {build_windows_identity_probe_script()}",
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


def build_domain_join_script(
    domain: str,
    username: str,
    password: str,
    ou: str | None,
    new_name: str,
) -> str:
    ou_clause = f" -OUPath {ps_single_quote(ou)}" if ou else ""
    return (
        f"$secpw = ConvertTo-SecureString {ps_single_quote(password)} -AsPlainText -Force; "
        f"$cred = New-Object System.Management.Automation.PSCredential({ps_single_quote(username)}, $secpw); "
        f"Add-Computer -DomainName {ps_single_quote(domain)} "
        f"-NewName {ps_single_quote(new_name)}{ou_clause} -Credential $cred "
        "-Force -ErrorAction Stop | Out-Null; 'DOMAIN-JOINED'"
    )


# ── helpers ──────────────────────────────────────────────────────────────────

_TIMEOUT_KEY_MAP: dict[str, tuple[str, ...]] = {
    "clone_vm": ("clone_minutes",),
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
                stage_key == "wait_for_tools"
                and ctx.request.source_type == VmSourceType.BLANK
                and ctx.request.guest.iso_id is not None
            ):
                return max(configured, 7200.0)
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
        status="SKIPPED",
        output=(
            f"{operation} is not applicable to a blank VM. The VM is left powered off "
            "until an operating system is installed."
        ),
    )


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
        allowed_roots = [
            str(root).lower() for root in rows.get(SETTING_ALLOWED_INSTALLER_ROOTS, [])
        ]
        outside = [
            app.installer_path
            for app in applications
            if not any(app.installer_path.lower().startswith(root) for root in allowed_roots)
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
    existing_id = await ctx.vmware.resolve_vm_id(ctx.target, ctx.vm_name)
    if existing_id is not None:
        ctx.vm_ref = VmRef(id=existing_id, name=ctx.vm_name)
        return StageOutcome(
            status="SKIPPED",
            output=f"A VM named '{ctx.vm_name}' already exists — reusing it instead of cloning again.",
            artifacts={"vm_id": existing_id, "vm_name": ctx.vm_name},
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
        )
        vm_ref = await ctx.vmware.create_blank_vm(ctx.target, blank_spec)
        media = " with the selected ISO mounted" if r.guest.iso_id else " without installation media"
        output = f"Created blank virtual machine '{vm_ref.name}'{media} in powered-off state."
    ctx.vm_ref = vm_ref
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
        artifacts={"vm_id": vm_ref.id, "vm_name": vm_ref.name},
    )


async def stage_configure_hardware(ctx: JobRunContext) -> StageOutcome:
    vm_id = _require_vm_id(ctx)
    hw = ctx.request.hardware
    await ctx.vmware.configure_hardware(
        ctx.target, vm_id,
        cpu=hw.cpu, memory_mb=hw.memory_mb, disks=list(hw.disks),
        firmware=hw.firmware, secure_boot=hw.secure_boot,
    )
    disk_summary = ", ".join(f"{d.size_gb} GB {d.provisioning.value}" for d in hw.disks)
    return StageOutcome(
        output=f"CPU: {hw.cpu} vCPU\nMemory: {hw.memory_mb} MB\nFirmware: {hw.firmware.value}"
               f"{'' if not hw.secure_boot else ' + Secure Boot'}\nDisks: {disk_summary}",
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
    skipped = _blank_guest_skip(ctx, "Unattended Windows installation")
    if skipped:
        return skipped
    vm_id = _require_vm_id(ctx)
    credentials = await ctx.resolve_guest_credentials()
    guest = ctx.request.guest
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
        )
    )
    # The media contains a plaintext Windows Setup password by necessity. It is
    # held only in memory here, uploaded directly, and removed after Tools starts.
    media = build_unattend_iso(xml)
    datastore_id = next(
        (disk.datastore_id for disk in ctx.request.hardware.disks if disk.datastore_id),
        None,
    )
    ref = await ctx.vmware.attach_temporary_iso(
        ctx.target,
        vm_id,
        datacenter_id=ctx.request.compute.datacenter_id,
        datastore_id=datastore_id,
        file_name=f"infraops-{ctx.job_id}.iso",
        content=media,
    )
    return StageOutcome(
        output=(
            "Temporary answer media attached. Windows Setup will configure the selected "
            "administrator, locale, keyboard layout and computer name without OOBE prompts."
        ),
        artifacts={"datastore_path": ref.datastore_path},
    )


async def stage_power_on(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Power-on")
    if skipped:
        return skipped
    vm_id = _require_vm_id(ctx)
    info = await ctx.vmware.get_vm_info(ctx.target, ctx.vm_name)
    if info is not None and info.power_state == "poweredOn":
        return StageOutcome(status="SKIPPED", output="VM is already powered on.")
    await ctx.vmware.power_on(ctx.target, vm_id)
    return StageOutcome(output="Power-on task completed.")


async def stage_wait_for_tools(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "VMware Tools readiness")
    if skipped:
        return skipped
    vm_id = _require_vm_id(ctx)
    timeout = await effective_timeout_seconds(ctx, "wait_for_tools")
    await ctx.vmware.wait_for_tools(ctx.target, vm_id, timeout)
    return StageOutcome(output=f"VMware Tools reported ready (waited up to {int(timeout)} s).")


async def stage_cleanup_unattended_media(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Temporary unattended media cleanup")
    if skipped:
        return skipped
    prepare = ctx.steps_by_key.get("prepare_unattended_install")
    datastore_path = ((prepare.artifacts or {}).get("datastore_path") if prepare else None)
    if not datastore_path:
        return StageOutcome(status="SKIPPED", output="No temporary unattended media was recorded.")
    await ctx.vmware.remove_temporary_iso(
        ctx.target,
        _require_vm_id(ctx),
        datacenter_id=ctx.request.compute.datacenter_id,
        datastore_path=str(datastore_path),
    )
    return StageOutcome(output="Temporary unattended answer media was detached and deleted.")


async def stage_configure_guest_network(ctx: JobRunContext) -> StageOutcome:
    skipped = _blank_guest_skip(ctx, "Guest network configuration")
    if skipped:
        return skipped
    credentials = await ctx.resolve_guest_credentials()
    net = ctx.request.network
    if net.mode == IpMode.STATIC and net.ipv4 is not None:
        ipv4 = net.ipv4
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
    result = await ctx.guest_ops.run_program(
        ctx.target, ctx.vm_name, credentials, POWERSHELL_PATH,
        f"-NoProfile -NonInteractive -Command {script}", timeout,
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
        result = await ctx.guest_ops.run_program(
            ctx.target, ctx.vm_name, credentials, POWERSHELL_PATH,
            f"-NoProfile -NonInteractive -Command {gw_script}", 120,
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
                "-ErrorAction SilentlyContinue; if ($r) { 'DNS-OK' } else { exit 1 }"
            )
            result = await ctx.guest_ops.run_program(
                ctx.target, ctx.vm_name, credentials, POWERSHELL_PATH,
                f"-NoProfile -NonInteractive -Command {dns_script}", 120,
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
        probe = (
            "'$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { "
            "$_.IPAddress -notlike '169.254*' -and $_.IPAddress -ne '127.0.0.1' "
            '} | Select-Object -First 1).IPAddress; if ($ip) { "DHCP-IP:$ip" } '
            "else { exit 1 }"
        )
        result = await ctx.guest_ops.run_program(
            ctx.target, ctx.vm_name, credentials, POWERSHELL_PATH,
            f"-NoProfile -NonInteractive -Command {probe}", 180,
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

    probe = await ctx.guest_ops.run_program(
        ctx.target, ctx.vm_name, credentials, POWERSHELL_PATH,
        "-NoProfile -NonInteractive -Command $env:COMPUTERNAME", 60,
    )
    current = (probe.stdout or "").strip().upper().splitlines()[-1] if probe.stdout else ""
    if probe.succeeded and current == desired:
        return StageOutcome(
            status="SKIPPED",
            output=f"Windows computer name already set to '{desired}'.",
            artifacts=identity_artifacts,
        )

    result = await ctx.guest_ops.run_program(
        ctx.target, ctx.vm_name, credentials, POWERSHELL_PATH,
        f"-NoProfile -NonInteractive -Command {build_rename_script(desired)}", 180,
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

    script = build_domain_join_script(
        join.domain,
        username,
        password,
        join.ou,
        desired_name,
    )
    timeout = await effective_timeout_seconds(ctx, "join_domain")
    result = await ctx.guest_ops.run_program(
        ctx.target, ctx.vm_name, credentials, POWERSHELL_PATH,
        f"-NoProfile -NonInteractive -Command {script}", timeout,
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
    await ctx.guest_ops.run_program(
        ctx.target, ctx.vm_name, credentials, POWERSHELL_PATH,
        "-NoProfile -NonInteractive -Command Restart-Computer -Force", 60,
    )
    await asyncio.sleep(6)
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
    while dt.datetime.now(dt.UTC) < deadline:
        attempts += 1
        probe = await ctx.guest_ops.run_program(
            ctx.target, ctx.vm_name, credentials, CMD_PATH, "/c exit 0", 60,
        )
        if probe.succeeded:
            return StageOutcome(output=f"Guest responsive after restart ({attempts} probe(s)).")
        await asyncio.sleep(10)
    raise InfraOperationError(
        "The guest did not become responsive after the scheduled restart.",
        reason="Guest operations probes timed out after 10 minutes.",
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
