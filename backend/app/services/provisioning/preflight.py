"""Dry-run preflight validation.

Performs every check that can be executed WITHOUT modifying infrastructure
and produces the report rendered on the wizard's review screen. Provisioning
is blocked whenever any blocking check fails.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InfraOperationError
from app.core.logging import get_logger
from app.models.applications import Application
from app.models.certificates import CertificatePackage
from app.models.infrastructure import VCenterConnection
from app.schemas.provisioning import (
    CheckStatus,
    IpMode,
    PreflightCheck,
    PreflightReport,
    ProvisioningRequest,
    VmSourceType,
)
from app.secrets.base import SecretNotFoundError
from app.secrets.service import SecretsService, get_secrets_service
from app.services.applications.resolver import AppNode, resolve_install_order
from app.services.network.conflict import (
    DnsForwardProvider,
    IcmpPingProvider,
    VMwareInventoryProvider,
    run_conflict_check,
)
from app.services.network.validation import validate_static_ipv4
from app.services.settings_store import (
    SETTING_ALLOWED_INSTALLER_ROOTS,
    SETTING_VM_NAME_POLICY,
    load_effective,
)
from app.services.vmware.base import VCenterTarget, VMwareService

log = get_logger(__name__)


def _safe_infrastructure_failure(exc: Exception, fallback: str) -> str:
    if isinstance(exc, InfraOperationError):
        return (
            f"{exc.human_message} Reason: {exc.reason} "
            f"Recommended action: {exc.recommended_action}"
        )
    log.exception("Unexpected preflight integration failure")
    return fallback


class PreflightValidator:
    def __init__(
        self,
        db: AsyncSession,
        vmware: VMwareService,
        secrets: SecretsService | None = None,
    ) -> None:
        self._db = db
        self._vmware = vmware
        self._secrets = secrets or get_secrets_service()

    async def validate(
        self, request: ProvisioningRequest, *, run_ip_conflict_checks: bool = True
    ) -> PreflightReport:
        checks: list[PreflightCheck] = []

        def add(code: str, label: str, status: CheckStatus, detail: str = "", blocking: bool = True):
            checks.append(PreflightCheck(code=code, label=label, status=status,
                                         detail=detail, blocking=blocking))

        settings_rows = await load_effective(self._db)

        # ── vCenter connection ───────────────────────────────────────────────
        target: VCenterTarget | None = None
        try:
            row = await self._db.get(VCenterConnection, request.compute.vcenter_id)
            if row is None:
                add("vcenter", "vCenter connection", CheckStatus.FAIL,
                    "The selected vCenter connection no longer exists.")
            elif not row.enabled:
                add("vcenter", "vCenter connection", CheckStatus.FAIL,
                    f"vCenter '{row.name}' is disabled.")
            else:
                target = VCenterTarget(
                    id=str(row.id), name=row.name, host=row.host, port=row.port,
                    username_secret_ref=row.username_secret_ref,
                    password_secret_ref=row.password_secret_ref,
                    verify_ssl=row.verify_ssl,
                )
                result = await self._vmware.test_connection(target)
                if result.ok:
                    add("vcenter", "vCenter connection successful", CheckStatus.PASS,
                        f"{row.name} ({row.host})")
                else:
                    add("vcenter", "vCenter connection", CheckStatus.FAIL, result.detail)
        except Exception as exc:  # noqa: BLE001
            add(
                "vcenter",
                "vCenter connection",
                CheckStatus.FAIL,
                _safe_infrastructure_failure(
                    exc,
                    "The vCenter connection could not be tested. Try again or contact an administrator.",
                ),
            )

        # ── Infrastructure discovery ─────────────────────────────────────────
        if target is not None:
            await self._check_infrastructure(request, target, add)

        # ── VM name policy & uniqueness ──────────────────────────────────────
        policy = str(settings_rows.get(SETTING_VM_NAME_POLICY) or "")
        if policy:
            try:
                matches = re.fullmatch(policy, request.vm.name) is not None
            except re.error:
                matches = True
                add("name_policy", "VM name policy", CheckStatus.WARN,
                    "The configured naming policy regex is invalid — check Administration → Settings.",
                    blocking=False)
            if matches:
                add("name_policy", "VM name matches naming policy", CheckStatus.PASS)
            else:
                add("name_policy", "VM name policy", CheckStatus.FAIL,
                    f"'{request.vm.name}' does not match the configured naming policy.")

        if target is not None:
            try:
                exists = await self._vmware.vm_exists(target, request.vm.name)
                if exists:
                    add("vm_name_unique", "VM name is available", CheckStatus.FAIL,
                        f"A VM named '{request.vm.name}' already exists.")
                else:
                    add("vm_name_unique", "VM name is available", CheckStatus.PASS)
            except Exception as exc:  # noqa: BLE001
                add(
                    "vm_name_unique",
                    "VM name uniqueness",
                    CheckStatus.FAIL,
                    _safe_infrastructure_failure(
                        exc,
                        "VM name availability could not be checked. Try again.",
                    ),
                )

        # ── Network addressing ───────────────────────────────────────────────
        if request.network.mode == IpMode.STATIC and request.network.ipv4 is not None:
            ipv4 = request.network.ipv4
            issues = validate_static_ipv4(ipv4.address, ipv4.prefix, ipv4.gateway, ipv4.dns_servers)
            if issues:
                add("ip_syntax", "IP address syntax", CheckStatus.FAIL,
                    "; ".join(issue.message for issue in issues))
            else:
                add("ip_syntax", "IP address syntax valid", CheckStatus.PASS)

            if run_ip_conflict_checks and not issues and target is not None:
                providers = [
                    IcmpPingProvider(),
                    DnsForwardProvider(),
                    VMwareInventoryProvider(self._vmware, target),
                ]
                try:
                    report = await run_conflict_check(ipv4.address, ipv4.prefix, providers)
                    if report.conflict_detected:
                        conflicts = [p.detail for p in report.providers
                                     if p.status.value == "CONFLICT_DETECTED"]
                        add("ip_conflict", "IP conflict check", CheckStatus.FAIL,
                            " ".join(conflicts))
                    else:
                        add("ip_conflict", "No conflict detected", CheckStatus.PASS,
                            report.confidence_note)
                except Exception as exc:  # noqa: BLE001
                    add("ip_conflict", "IP conflict check", CheckStatus.WARN,
                        _safe_infrastructure_failure(
                            exc,
                            "One or more conflict providers could not be checked.",
                        ), blocking=False)

        # ── Certificates ─────────────────────────────────────────────────────
        await self._check_certificates(request, add)

        # ── Applications & dependencies ──────────────────────────────────────
        await self._check_applications(request, add)

        # ── Installer repository reachability policy ─────────────────────────
        allowed_roots = [str(root).lower() for root in
                         (settings_rows.get(SETTING_ALLOWED_INSTALLER_ROOTS) or [])]
        selected_applications = await self._selected_applications(request)
        outside = [
            app.installer_path
            for app in selected_applications
            if allowed_roots and not any(app.installer_path.lower().startswith(root) for root in allowed_roots)
        ]
        if selected_applications and not allowed_roots:
            add("installer_policy", "Installer repository policy", CheckStatus.FAIL,
                "No approved installer repository roots are configured. Configure at least "
                "one root before selecting applications.")
        elif outside:
            add("installer_policy", "Installer locations approved", CheckStatus.FAIL,
                f"{len(outside)} installer path(s) are outside the approved software "
                "repository roots configured by administrators.")
        else:
            add("installer_policy", "Installer locations approved", CheckStatus.PASS)

        # ── Credential references ────────────────────────────────────────────
        await self._check_credentials(request, add)

        blocking_failures = [c for c in checks if c.status == CheckStatus.FAIL and c.blocking]
        warnings = [c for c in checks if c.status == CheckStatus.WARN]
        ready = not blocking_failures
        summary = (
            f"Ready to provision ({len(checks)} checks, {len(warnings)} warning(s))."
            if ready
            else f"Not ready — {len(blocking_failures)} blocking problem(s) must be resolved."
        )
        return PreflightReport(ready=ready, checks=checks, summary=summary)

    # ── section helpers ──────────────────────────────────────────────────────

    async def _check_infrastructure(self, request: ProvisioningRequest, target: VCenterTarget, add) -> None:
        try:
            datacenters = {dc.id: dc for dc in await self._vmware.get_datacenters(target)}
            dc = datacenters.get(request.compute.datacenter_id)
            if dc is None:
                add("datacenter", "Datacenter exists", CheckStatus.FAIL,
                    f"Datacenter '{request.compute.datacenter_id}' was not found.")
                return
            add("datacenter", "Datacenter exists", CheckStatus.PASS, dc.name)

            clusters = {c.id: c for c in await self._vmware.get_clusters(target, dc.id)}
            cluster = clusters.get(request.compute.cluster_id)
            if cluster is None:
                add("cluster", "Compute target exists", CheckStatus.FAIL,
                    f"Compute target '{request.compute.cluster_id}' was not found in {dc.name}.")
                return
            add("cluster", "Compute target available", CheckStatus.PASS,
                f"{cluster.name} ({cluster.hosts_count} host(s), DRS {'on' if cluster.drs_enabled else 'off'})")

            hosts = {h.id: h for h in await self._vmware.get_hosts(target, cluster.id)}
            if request.compute.host_id:
                host = hosts.get(request.compute.host_id)
                if host is None:
                    add("host", "Host availability", CheckStatus.FAIL,
                        "The manually selected host was not found in the compute target.")
                elif not host.available_for_provisioning:
                    add("host", "Host availability", CheckStatus.FAIL,
                        f"Host '{host.name}' is not available "
                        f"(state={host.connection_state}, maintenance={host.maintenance_mode}).")
                else:
                    add("host", "Host available", CheckStatus.PASS, host.name)

            if request.compute.resource_pool_id:
                pools = {p.id for p in await self._vmware.get_resource_pools(target, cluster.id)}
                if request.compute.resource_pool_id not in pools:
                    add("resource_pool", "Resource pool exists", CheckStatus.FAIL,
                        "The selected resource pool does not belong to this cluster.")
                else:
                    add("resource_pool", "Resource pool exists", CheckStatus.PASS)

            datastores = {d.id: d for d in await self._vmware.get_datastores(target, cluster.id)}
            total_gb = request.total_disk_gb
            explicit_ids = {disk.datastore_id for disk in request.hardware.disks if disk.datastore_id}
            if explicit_ids:
                missing = [ds_id for ds_id in explicit_ids if ds_id not in datastores]
                if missing:
                    add("datastore", "Datastore availability", CheckStatus.FAIL,
                        f"Datastore(s) not found: {', '.join(missing)}")
                else:
                    add("datastore", "Datastore availability", CheckStatus.PASS,
                        ", ".join(datastores[ds_id].name for ds_id in explicit_ids))
            candidates = [d for d in datastores.values() if d.accessible]
            best_free = max((d.free_gb for d in candidates), default=0.0)
            if best_free < total_gb:
                add("datastore_capacity", "Datastore capacity", CheckStatus.FAIL,
                    f"The request requires approximately {total_gb} GB but the largest "
                    f"accessible datastore has {best_free:.0f} GB free.")
            else:
                add("datastore_capacity", "Datastore has sufficient capacity",
                    CheckStatus.PASS, f"{total_gb} GB required")

            networks = {n.id: n for n in await self._vmware.get_networks(target, dc.id)}
            network = networks.get(request.network.network_id)
            if network is None:
                add("network", "Network exists", CheckStatus.FAIL,
                    f"Port group '{request.network.network_id}' was not found.")
            else:
                add("network", "Network exists", CheckStatus.PASS,
                    f"{network.name} ({network.type})")

            if request.source_type == VmSourceType.TEMPLATE:
                templates = {t.id: t for t in await self._vmware.get_templates(target, dc.id)}
                template = templates.get(request.guest.template_id)
                if template is None:
                    add("template", "Template accessible", CheckStatus.FAIL,
                        f"Template '{request.guest.template_id}' was not found.")
                else:
                    add("template", "OVF/OVA package accessible", CheckStatus.PASS,
                        f"{template.name} ({template.type})")
            else:
                if request.guest.iso_id:
                    isos = {
                        image.id: image
                        for image in await self._vmware.get_isos(target, dc.id)
                    }
                    image = isos.get(request.guest.iso_id)
                    if image is None:
                        add("iso", "ISO available", CheckStatus.FAIL,
                            "The selected ISO was not found in this datacenter.")
                    elif image.datastore_id not in datastores or not datastores[
                        image.datastore_id
                    ].accessible:
                        add("iso", "ISO accessible to cluster", CheckStatus.FAIL,
                            f"{image.name} is stored on a datastore unavailable to this cluster.")
                    else:
                        add("iso", "ISO available", CheckStatus.PASS,
                            f"{image.name} on {image.datastore_name}")
                else:
                    add("iso", "Installation media", CheckStatus.PASS,
                        "No ISO will be mounted.")
        except Exception as exc:  # noqa: BLE001
            add(
                "infrastructure",
                "Infrastructure discovery",
                CheckStatus.FAIL,
                _safe_infrastructure_failure(
                    exc,
                    "Infrastructure inventory could not be loaded. Try again.",
                ),
            )

    async def _selected_applications(self, request: ProvisioningRequest) -> list[Application]:
        if not request.application_ids:
            return []
        result = await self._db.execute(
            select(Application).where(Application.id.in_(request.application_ids))
        )
        return list(result.scalars().all())

    async def _check_certificates(self, request: ProvisioningRequest, add) -> None:
        if not request.certificate_package_ids:
            add("certificates", "Certificate packages", CheckStatus.PASS, "None selected.")
            return
        result = await self._db.execute(
            select(CertificatePackage).where(CertificatePackage.id.in_(request.certificate_package_ids))
        )
        packages = {str(p.id): p for p in result.scalars().all()}
        problems = []
        for package_id in request.certificate_package_ids:
            package = packages.get(str(package_id))
            if package is None:
                problems.append(f"Package '{package_id}' does not exist.")
            elif not package.enabled:
                problems.append(f"Package '{package.name}' is disabled.")
            elif not any(c.enabled for c in package.certificates):
                problems.append(f"Package '{package.name}' contains no enabled certificates.")
        if problems:
            add("certificates", "Certificate packages available", CheckStatus.FAIL,
                " ".join(problems))
        else:
            names = [packages[str(pid)].name for pid in request.certificate_package_ids]
            add("certificates", "Certificate packages available", CheckStatus.PASS,
                ", ".join(names))

    async def _check_applications(self, request: ProvisioningRequest, add) -> None:
        selected = await self._selected_applications(request)
        if not request.application_ids:
            add("applications", "Application definitions", CheckStatus.PASS, "None selected.")
            return
        found_ids = {app.id for app in selected}
        missing = [str(aid) for aid in request.application_ids if aid not in found_ids]
        disabled = [app.name for app in selected if not app.enabled]

        result = await self._db.execute(select(Application).where(Application.enabled.is_(True)))
        catalog: dict[uuid.UUID, AppNode] = {}
        for app in result.scalars().all():
            catalog[app.id] = AppNode(
                id=app.id,
                name=app.name,
                enabled=app.enabled,
                dependency_ids=frozenset(dep.depends_on_id for dep in app.dependencies),
            )

        ordered, errors = resolve_install_order(list(request.application_ids), catalog)
        problems = []
        if missing:
            problems.append(f"Applications not found: {', '.join(missing)}.")
        if disabled:
            problems.append(f"Applications disabled: {', '.join(disabled)}.")
        problems.extend(errors)

        if problems:
            add("applications", "Application definitions valid", CheckStatus.FAIL,
                " ".join(problems))
        else:
            add("applications", "Application definitions valid", CheckStatus.PASS,
                "Install order: " + " → ".join(node.name for node in ordered))

    async def _check_credentials(self, request: ProvisioningRequest, add) -> None:
        if request.source_type == VmSourceType.BLANK and request.guest.iso_id is None:
            add("credentials", "Guest credentials", CheckStatus.PASS,
                "Not required for a powered-off blank VM.")
            return
        bases = [request.guest.credential_secret_ref]
        if request.guest.domain_join:
            bases.append(request.guest.domain_join.credential_secret_ref)
        problems = []
        for base in bases:
            for suffix in ("username", "password"):
                try:
                    await self._secrets.get_secret(f"{base}/{suffix}")
                except SecretNotFoundError:
                    problems.append(f"Secret '{base}/{suffix}' is not resolvable by the "
                                    f"'{self._secrets.provider_name}' provider.")
                except Exception:  # noqa: BLE001
                    log.exception("Credential reference preflight check failed")
                    problems.append(
                        f"Secret '{base}/{suffix}' could not be checked. Contact an administrator."
                    )
        if problems:
            if any(
                problem.startswith(f"Secret '{request.guest.credential_secret_ref}/")
                for problem in problems
            ):
                problems.append(
                    "Select a configured Windows provisioning administrator credential. "
                    "It is required before network configuration and domain join."
                )
            add("credentials", "Credential references resolvable", CheckStatus.FAIL,
                " ".join(problems))
        else:
            add("credentials", "Credential references resolvable", CheckStatus.PASS)
