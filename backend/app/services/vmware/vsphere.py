"""Production VMware vSphere adapter (pyvmomi).

ENVIRONMENT DEPENDENT — requires a reachable vCenter and the ``pyvmomi``
package. All blocking pyvmomi calls are executed in worker threads; every
failure is translated into an :class:`InfraOperationError` carrying a human
message, a reason, a recommended action and the preserved technical detail.

Credentials are resolved through the secrets provider at connect time and are
never cached, logged, or exposed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
import threading
import time

from app.core.config import get_settings
from app.core.errors import InfraOperationError, NotFoundError, ServiceUnavailableError
from app.core.logging import get_logger
from app.core.metrics import vcenter_api_errors_total
from app.schemas.infrastructure import (
    ClusterOut,
    ConnectionTestResult,
    DatacenterOut,
    DatastoreClusterOut,
    DatastoreOut,
    HostOut,
    NetworkOut,
    ResourcePoolOut,
    TemplateOut,
)
from app.schemas.provisioning import AdapterType, FirmwareType
from app.secrets.service import SecretsService
from app.services.vmware.base import (
    BlankVmSpec,
    CloneSpec,
    PowerStateInfo,
    VCenterTarget,
    VmRef,
    VMwareService,
)

log = get_logger(__name__)

try:  # pyvmomi is optional — mock deployments never need it.
    from pyVim import connect as pyvim_connect
    from pyVmomi import vim, vmodl

    HAS_PYVMOMI = True
except ImportError:  # pragma: no cover - exercised only without pyvmomi installed
    HAS_PYVMOMI = False

_CONNECTION_TTL_SECONDS = 1800.0
_TOOLS_POLL_INTERVAL = 5.0
_TASK_POLL_INTERVAL = 0.5


def _require_pyvmomi() -> None:
    if not HAS_PYVMOMI:  # pragma: no cover
        raise ServiceUnavailableError(
            "The real VMware adapter requires the 'pyvmomi' package. "
            "Install it or run with INFRASTRUCTURE_MODE=mock."
        )


def _wrap(operation: str, exc: Exception, *, retryable: bool = True) -> InfraOperationError:
    vcenter_api_errors_total.inc(operation=operation)
    if isinstance(exc, InfraOperationError):
        return exc
    if HAS_PYVMOMI and isinstance(exc, vim.fault.NoPermission):
        return InfraOperationError(
            "vCenter denied a required operation due to insufficient permissions.",
            reason="The service account lacks the vCenter privilege needed for this operation.",
            recommended_action=(
                "Grant the InfraOps service account the required vCenter privileges "
                "(see docs/vmware-integration.md) and retry."
            ),
            technical_detail=str(exc),
            retryable=False,
        )
    if HAS_PYVMOMI and isinstance(exc, (vim.fault.InvalidLogin,)):
        return InfraOperationError(
            "vCenter rejected the service account credentials.",
            reason="Authentication failed (InvalidLogin).",
            recommended_action="Verify the credential secret references on the vCenter connection.",
            technical_detail=str(exc),
            retryable=False,
        )
    return InfraOperationError(
        f"vCenter operation '{operation}' failed.",
        reason="The vSphere API returned an unexpected error.",
        recommended_action="Check vCenter health and retry the failed stage.",
        technical_detail=f"{type(exc).__name__}: {exc}",
        retryable=retryable,
    )


class _ConnectionCache:
    """Thread-safe cache of live ServiceInstance objects keyed by vCenter id."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()

    def get(self, vcenter_id: str):
        with self._lock:
            entry = self._sessions.get(vcenter_id)
            if entry is None:
                return None
            instance, created = entry
            if time.monotonic() - created > _CONNECTION_TTL_SECONDS:
                del self._sessions[vcenter_id]
                return None
            return instance

    def put(self, vcenter_id: str, instance) -> None:
        with self._lock:
            self._sessions[vcenter_id] = (instance, time.monotonic())

    def evict(self, vcenter_id: str) -> None:
        with self._lock:
            entry = self._sessions.pop(vcenter_id, None)
        if entry is not None:
            try:
                pyvim_connect.Disconnect(entry[0])
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass


class VsphereVMwareService(VMwareService):
    name = "vsphere"

    def __init__(self, secrets: SecretsService) -> None:
        _require_pyvmomi()
        self._secrets = secrets
        self._cache = _ConnectionCache()

    # ── Connection handling ──────────────────────────────────────────────────

    async def _get_session(self, target: VCenterTarget):
        cached = self._cache.get(target.id)
        if cached is not None:
            return cached
        username, password = await self._secrets.get_credentials(
            target.username_secret_ref, target.password_secret_ref
        )
        try:
            instance = await asyncio.to_thread(self._connect_blocking, target, username, password)
        except InfraOperationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap("connect", exc, retryable=True) from exc
        self._cache.put(target.id, instance)
        return instance

    def _connect_blocking(self, target: VCenterTarget, username: str, password: str):
        ca_file = get_settings().vcenter_ca_file or None
        context = ssl.create_default_context(cafile=ca_file)
        if not target.verify_ssl:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        try:
            instance = pyvim_connect.SmartConnect(
                host=target.host,
                port=target.port,
                user=username,
                pwd=password,
                sslContext=context,
                disableSslCertValidation=not target.verify_ssl,
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap("connect", exc) from exc
        if instance is None:
            raise InfraOperationError(
                f"Could not establish a session with vCenter '{target.host}'.",
                reason="SmartConnect returned no session.",
                recommended_action="Verify host/port and service account credentials.",
                retryable=True,
            )
        return instance

    async def _with_session(self, target: VCenterTarget, fn, *, operation: str = "session-call"):
        """Run ``fn(si)`` in a thread; evict the session on connection errors."""
        si = await self._get_session(target)
        try:
            return await asyncio.to_thread(fn, si)
        except (InfraOperationError, NotFoundError):
            raise
        except (vim.fault.NoPermission, vim.fault.InvalidLogin) as exc:
            log.warning(
                "vCenter operation denied operation=%s vcenter_id=%s host=%s error_type=%s",
                operation,
                target.id,
                target.host,
                type(exc).__name__,
            )
            raise _wrap(operation, exc) from exc
        except (ConnectionError, OSError, vmodl.RuntimeFault) as exc:
            self._cache.evict(target.id)
            log.exception(
                "vCenter connection failed operation=%s vcenter_id=%s host=%s",
                operation,
                target.id,
                target.host,
            )
            raise _wrap(operation, exc) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "vCenter operation failed operation=%s vcenter_id=%s host=%s",
                operation,
                target.id,
                target.host,
            )
            raise _wrap(operation, exc) from exc

    # ── pyvmomi helpers (blocking, run inside threads) ───────────────────────

    @staticmethod
    def _content(si):
        return si.RetrieveContent()

    @staticmethod
    def _wait_for_task(task) -> None:
        while task.info.state in (vim.TaskInfo.State.running, vim.TaskInfo.State.queued):
            time.sleep(_TASK_POLL_INTERVAL)
        if task.info.state == vim.TaskInfo.State.error:
            error = task.info.error
            raise error if error is not None else RuntimeError("Task failed without an error object.")

    @staticmethod
    def _container_view(content, vimtype):
        return content.viewManager.CreateContainerView(content.rootFolder, [vimtype], True)

    @classmethod
    def _find_by_moref(cls, content, moref_id: str):
        """Resolve a managed object reference string like 'domain-c7'."""
        view = cls._container_view(content, vim.ManagedEntity)
        try:
            for entity in view.view:
                if entity._moId == moref_id:
                    return entity
        finally:
            view.Destroy()
        return None

    @classmethod
    def _find_vm_by_name(cls, content, name: str):
        view = cls._container_view(content, vim.VirtualMachine)
        try:
            for vm in view.view:
                if vm.name.lower() == name.lower():
                    return vm
        finally:
            view.Destroy()
        return None

    @staticmethod
    def _owning_datacenter(entity):
        """Resolve an inventory entity's datacenter through its folder ancestry."""
        current = entity
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            parent = getattr(current, "parent", None)
            if parent is None:
                return None
            vm_folder = getattr(parent, "vmFolder", None)
            if (
                vm_folder is current
                or (
                    getattr(vm_folder, "_moId", None) is not None
                    and getattr(vm_folder, "_moId", None) == getattr(current, "_moId", None)
                )
            ):
                return parent
            current = parent
        return None

    @staticmethod
    def _host_usable(host) -> bool:
        runtime = host.runtime
        return (
            runtime.connectionState == vim.HostSystem.ConnectionState.connected
            and not runtime.inMaintenanceMode
        )

    @staticmethod
    def _cluster_datastores(cluster) -> dict:
        datastores: dict = {}
        for host in cluster.host:
            for datastore in host.datastore:
                if datastore._moId not in datastores:
                    datastores[datastore._moId] = datastore
        return datastores

    # ── Discovery ────────────────────────────────────────────────────────────

    async def test_connection(self, target: VCenterTarget) -> ConnectionTestResult:
        started = time.perf_counter()
        try:
            await self._get_session(target)
        except InfraOperationError as exc:
            return ConnectionTestResult(ok=False, detail=exc.human_message)
        latency = (time.perf_counter() - started) * 1000
        return ConnectionTestResult(
            ok=True, latency_ms=round(latency, 1), detail=f"Authenticated to {target.host}."
        )

    async def get_datacenters(self, target: VCenterTarget) -> list[DatacenterOut]:
        def op(si):
            content = self._content(si)
            view = self._container_view(content, vim.Datacenter)
            try:
                return [
                    DatacenterOut(id=dc._moId, name=dc.name, vcenter_id=target.id) for dc in view.view
                ]
            finally:
                view.Destroy()

        return sorted(await self._with_session(target, op), key=lambda d: d.name)

    async def get_clusters(self, target: VCenterTarget, datacenter_id: str) -> list[ClusterOut]:
        def op(si):
            content = self._content(si)
            datacenter = self._find_by_moref(content, datacenter_id)
            if datacenter is None:
                raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")
            clusters = []
            for entity in datacenter.hostFolder.childEntity:
                if isinstance(entity, vim.ClusterComputeResource):
                    clusters.append(
                        ClusterOut(
                            id=entity._moId,
                            name=entity.name,
                            datacenter_id=datacenter_id,
                            drs_enabled=bool(entity.configurationEx.drsConfig.enabled),
                            hosts_count=len(entity.host),
                            total_cpu_cores=sum(h.hardware.cpuInfo.numCpuCores for h in entity.host),
                            total_memory_gb=round(
                                sum(h.hardware.memorySize for h in entity.host) / 1024**3, 1
                            ),
                        )
                    )
            return clusters

        return sorted(await self._with_session(target, op), key=lambda c: c.name)

    async def get_hosts(self, target: VCenterTarget, cluster_id: str) -> list[HostOut]:
        def op(si):
            content = self._content(si)
            cluster = self._find_by_moref(content, cluster_id)
            if cluster is None:
                raise NotFoundError(f"Cluster '{cluster_id}' does not exist.")
            hosts = []
            for host in cluster.host:
                # HostSystem exposes quick statistics through summary, not as
                # a direct HostSystem attribute. Accessing host.quickStats made
                # the entire discovery request fail on real vCenter sessions.
                quick = host.summary.quickStats
                cpu_pct = 0.0
                mem_pct = 0.0
                try:
                    if host.hardware.cpuInfo.numCpuCores:
                        cpu_pct = round((quick.overallCpuUsage or 0) / (
                            host.hardware.cpuInfo.numCpuCores * host.hardware.cpuInfo.hz / 1e6
                        ) * 100, 1)
                    if host.hardware.memorySize:
                        used_bytes = (quick.overallMemoryUsage or 0) * 1024**2
                        mem_pct = round(used_bytes / host.hardware.memorySize * 100, 1)
                except (TypeError, ZeroDivisionError):
                    pass
                hosts.append(
                    HostOut(
                        id=host._moId,
                        name=host.name,
                        connection_state=str(host.runtime.connectionState),
                        maintenance_mode=bool(host.runtime.inMaintenanceMode),
                        cpu_usage_percent=cpu_pct,
                        memory_usage_percent=mem_pct,
                        available_for_provisioning=self._host_usable(host),
                    )
                )
            return hosts

        return sorted(await self._with_session(target, op), key=lambda h: h.name)

    async def get_resource_pools(self, target: VCenterTarget, cluster_id: str) -> list[ResourcePoolOut]:
        def op(si):
            content = self._content(si)
            cluster = self._find_by_moref(content, cluster_id)
            if cluster is None:
                raise NotFoundError(f"Cluster '{cluster_id}' does not exist.")
            pools = [ResourcePoolOut(id=cluster.resourcePool._moId, name="Resources", cluster_id=cluster_id)]

            def walk(pool):
                for child in getattr(pool, "resourcePool", []) or []:
                    pools.append(ResourcePoolOut(id=child._moId, name=child.name, cluster_id=cluster_id))
                    walk(child)

            walk(cluster.resourcePool)
            return pools

        return await self._with_session(target, op)

    async def get_datastores(self, target: VCenterTarget, cluster_id: str) -> list[DatastoreOut]:
        def op(si):
            content = self._content(si)
            cluster = self._find_by_moref(content, cluster_id)
            if cluster is None:
                raise NotFoundError(f"Cluster '{cluster_id}' does not exist.")
            results = []
            for datastore in self._cluster_datastores(cluster).values():
                summary = datastore.summary
                capacity = float(summary.capacity or 0) / 1024**3
                free = float(summary.freeSpace or 0) / 1024**3
                usage = round((capacity - free) / capacity * 100, 1) if capacity else 100.0
                ds_type = str(summary.type).upper()
                results.append(
                    DatastoreOut(
                        id=datastore._moId,
                        name=datastore.name,
                        type=ds_type,
                        capacity_gb=round(capacity, 1),
                        free_gb=round(free, 1),
                        usage_percent=usage,
                        accessible=bool(summary.accessible),
                        datastore_cluster_id=None,
                    )
                )
            return results

        return sorted(await self._with_session(target, op), key=lambda d: d.name)

    async def get_datastore_clusters(self, target: VCenterTarget, cluster_id: str) -> list[DatastoreClusterOut]:
        def op(si):
            content = self._content(si)
            cluster = self._find_by_moref(content, cluster_id)
            if cluster is None:
                raise NotFoundError(f"Cluster '{cluster_id}' does not exist.")
            pods = []
            seen_pod_ids = {ds.parent._moId for ds in self._cluster_datastores(cluster).values()
                            if getattr(ds, "parent", None) is not None
                            and isinstance(ds.parent, vim.StoragePod)}
            for pod_id in seen_pod_ids:
                pod = self._find_by_moref(content, pod_id)
                if pod is None:
                    continue
                capacity = float(pod.summary.capacity or 0) / 1024**3
                free = float(pod.summary.freeSpace or 0) / 1024**3
                pods.append(
                    DatastoreClusterOut(id=pod._moId, name=pod.name, capacity_gb=round(capacity, 1), free_gb=round(free, 1))
                )
            return pods

        return await self._with_session(target, op)

    async def get_networks(self, target: VCenterTarget, datacenter_id: str | None = None) -> list[NetworkOut]:
        def op(si):
            content = self._content(si)
            networks = []
            view = self._container_view(content, vim.Network)
            try:
                entities = list(view.view)
            finally:
                view.Destroy()
            for entity in entities:
                if isinstance(entity, vim.dvs.DistributedVirtualPortgroup):
                    networks.append(
                        NetworkOut(id=entity._moId, name=entity.name, type="DISTRIBUTED_PORT_GROUP")
                    )
                elif isinstance(entity, vim.Network):
                    networks.append(NetworkOut(id=entity._moId, name=entity.name, type="STANDARD_PORT_GROUP"))
            return networks

        return sorted(await self._with_session(target, op), key=lambda n: n.name)

    async def get_templates(self, target: VCenterTarget, datacenter_id: str | None = None) -> list[TemplateOut]:
        def op(si):
            content = self._content(si)
            templates = []
            stats = {
                "visible_vms": 0,
                "visible_templates": 0,
            }
            # Query from the inventory root and resolve the owning datacenter
            # from each VM's folder ancestry. This avoids vCenter-version and
            # folder-layout differences in datacenter-scoped container views.
            view = content.viewManager.CreateContainerView(
                content.rootFolder, [vim.VirtualMachine], True
            )
            try:
                for vm in view.view:
                    stats["visible_vms"] += 1
                    config = getattr(vm, "config", None)
                    summary_config = getattr(getattr(vm, "summary", None), "config", None)
                    is_template = bool(
                        getattr(config, "template", False)
                        or getattr(summary_config, "template", False)
                    )
                    if not is_template:
                        continue
                    stats["visible_templates"] += 1

                    owner = self._owning_datacenter(vm)
                    owner_id = getattr(owner, "_moId", None)
                    owner_name = getattr(owner, "name", None)
                    guest = (
                        getattr(config, "guestFullName", None)
                        or getattr(summary_config, "guestFullName", None)
                        or ""
                    )
                    family = "windows" if "windows" in guest.lower() else (
                        "linux" if any(k in guest.lower() for k in ("linux", "rhel", "ubuntu", "debian")) else "other"
                    )
                    hardware = getattr(config, "hardware", None)
                    disks = [
                        device for device in (getattr(hardware, "device", None) or [])
                        if isinstance(device, vim.vm.device.VirtualDisk)
                    ]
                    templates.append(
                        TemplateOut(
                            id=vm._moId,
                            name=vm.name,
                            os_family=family,
                            os_version=guest,
                            last_modified=getattr(config, "modifyDate", None),
                            description=getattr(config, "annotation", None) or "",
                            datacenter_id=owner_id,
                            datacenter_name=owner_name,
                            cpu=getattr(hardware, "numCPU", None),
                            memory_mb=getattr(hardware, "memoryMB", None),
                            disk_size_gb=round(sum(disk.capacityInKB for disk in disks) / 1024**2, 1),
                        )
                    )
            finally:
                view.Destroy()
            return templates, stats

        templates, stats = await self._with_session(
            target, op, operation="list-templates"
        )
        log_method = log.warning if not templates else log.info
        log_method(
            "vCenter template inventory complete vcenter_id=%s host=%s datacenter_id=%s "
            "visible_vms=%s visible_classic_templates=%s returned_templates=%s",
            target.id,
            target.host,
            datacenter_id or "all",
            stats["visible_vms"],
            stats["visible_templates"],
            len(templates),
        )
        return sorted(templates, key=lambda template: template.name)

    # ── Inventory queries ────────────────────────────────────────────────────

    async def vm_exists(self, target: VCenterTarget, vm_name: str) -> bool:
        def op(si):
            return self._find_vm_by_name(self._content(si), vm_name) is not None

        return await self._with_session(target, op)

    async def get_vm_info(self, target: VCenterTarget, vm_name: str) -> PowerStateInfo | None:
        def op(si):
            vm = self._find_vm_by_name(self._content(si), vm_name)
            if vm is None:
                return None
            guest = vm.guest
            ips: list[str] = []
            if guest.net:
                for nic in guest.net:
                    if nic.ipConfig and nic.ipConfig.ipAddress:
                        ips.extend(ip.ipAddress for ip in nic.ipConfig.ipAddress if ":" not in ip.ipAddress)
            if not ips and guest.ipAddress:
                ips.append(guest.ipAddress)
            return PowerStateInfo(
                power_state=str(vm.runtime.powerState),
                tools_status=str(guest.toolsStatus) if guest.toolsStatus else None,
                ip_addresses=ips,
                host_id=vm.runtime.host._moId if vm.runtime.host else None,
            )

        return await self._with_session(target, op)

    async def get_used_ips(self, target: VCenterTarget) -> dict[str, str]:
        def op(si):
            content = self._content(si)
            used: dict[str, str] = {}
            view = self._container_view(content, vim.VirtualMachine)
            try:
                for vm in view.view:
                    guest = vm.guest
                    ips: list[str] = []
                    if guest.net:
                        for nic in guest.net:
                            if nic.ipConfig and nic.ipConfig.ipAddress:
                                ips.extend(
                                    ip.ipAddress for ip in nic.ipConfig.ipAddress if ":" not in ip.ipAddress
                                )
                    if not ips and guest.ipAddress:
                        ips.append(guest.ipAddress)
                    for ip in ips:
                        used.setdefault(ip, vm.name)
            finally:
                view.Destroy()
            return used

        return await self._with_session(target, op)

    async def resolve_vm_id(self, target: VCenterTarget, vm_name: str) -> str | None:
        def op(si):
            vm = self._find_vm_by_name(self._content(si), vm_name)
            return vm._moId if vm is not None else None

        return await self._with_session(target, op)

    # ── Lifecycle operations ─────────────────────────────────────────────────

    async def clone_from_template(self, target: VCenterTarget, spec: CloneSpec) -> VmRef:
        def op(si):
            content = self._content(si)
            template = self._find_by_moref(content, spec.template_id)
            if template is None:
                raise InfraOperationError(
                    f"Template '{spec.template_id}' was not found on {target.host}.",
                    reason="Template removed or renamed after validation.",
                    recommended_action="Re-open the wizard and select an available template.",
                    retryable=False,
                )
            if self._find_vm_by_name(content, spec.vm_name) is not None:
                raise InfraOperationError(
                    f"A virtual machine named '{spec.vm_name}' already exists.",
                    reason="Duplicate VM name in the vCenter inventory.",
                    recommended_action="Choose a different VM name and resubmit the request.",
                    retryable=False,
                )

            cluster = self._find_by_moref(content, spec.cluster_id)
            if cluster is None:
                raise InfraOperationError(
                    f"Cluster '{spec.cluster_id}' was not found.",
                    reason="Cluster removed after validation.",
                    recommended_action="Re-select the target cluster and retry.",
                    retryable=False,
                )

            # Placement: explicit host > resource pool > cluster default.
            if spec.host_id:
                host = self._find_by_moref(content, spec.host_id)
                if host is None or host not in cluster.host or not self._host_usable(host):
                    raise InfraOperationError(
                        f"Host '{spec.host_id}' is not available for provisioning.",
                        reason="Host disconnected, in maintenance mode, or outside the selected cluster.",
                        recommended_action="Select a different host or use automatic placement.",
                        retryable=False,
                    )
                pool = host.parent.resourcePool
                if spec.resource_pool_id:
                    pool = self._find_by_moref(content, spec.resource_pool_id) or pool
            else:
                pool = cluster.resourcePool
                if spec.resource_pool_id:
                    pool = self._find_by_moref(content, spec.resource_pool_id) or pool
                host = None

            datastore = None
            if spec.datastore_id:
                datastore = self._find_by_moref(content, spec.datastore_id)

            relocation = vim.VirtualMachineRelocateSpec()
            relocation.pool = pool
            if host is not None:
                relocation.host = host
            if datastore is not None:
                relocation.datastore = datastore

            config = vim.VirtualMachineConfigSpec()
            config.numCPUs = spec.cpu
            config.memoryMB = spec.memory_mb
            config.annotation = spec.description
            if spec.firmware == FirmwareType.EFI:
                config.firmware = "efi"
                if spec.secure_boot:
                    config.bootOptions = vim.vm.BootOptions(efiSecureBootEnabled=True)

            clone_spec = vim.VirtualMachineCloneSpec()
            clone_spec.location = relocation
            clone_spec.config = config
            clone_spec.powerOn = False
            clone_spec.template = False

            # Clone beside the source template. This also works for templates
            # stored in nested VM folders; walking via parent.parent.vmFolder
            # only worked for templates directly under the datacenter VM folder.
            vm_folder = template.parent
            try:
                task = template.Clone(folder=vm_folder, name=spec.vm_name, spec=clone_spec)
                self._wait_for_task(task)
            except InfraOperationError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise _wrap("clone_vm", exc) from exc

            created = self._find_vm_by_name(content, spec.vm_name)
            if created is None:
                raise InfraOperationError(
                    f"Clone task completed but VM '{spec.vm_name}' was not found.",
                    reason="Inventory inconsistency after clone.",
                    recommended_action="Check recent tasks in vCenter and retry.",
                    retryable=True,
                )
            return VmRef(id=created._moId, name=created.name)

        log.info("vSphere clone: template=%s name=%s cluster=%s", spec.template_id, spec.vm_name, spec.cluster_id)
        return await self._with_session(target, op)

    async def create_blank_vm(self, target: VCenterTarget, spec: BlankVmSpec) -> VmRef:
        def op(si):
            content = self._content(si)
            if self._find_vm_by_name(content, spec.vm_name) is not None:
                raise InfraOperationError(
                    f"A virtual machine named '{spec.vm_name}' already exists.",
                    reason="Duplicate VM name in the vCenter inventory.",
                    recommended_action="Choose a different VM name and resubmit the request.",
                    retryable=False,
                )

            datacenter = self._find_by_moref(content, spec.datacenter_id)
            cluster = self._find_by_moref(content, spec.cluster_id)
            if datacenter is None or cluster is None:
                raise InfraOperationError(
                    "The selected datacenter or cluster was not found.",
                    reason="The placement inventory changed after validation.",
                    recommended_action="Re-open the wizard and select the infrastructure again.",
                    retryable=False,
                )

            host = None
            if spec.host_id:
                candidate = self._find_by_moref(content, spec.host_id)
                if candidate is None or candidate not in cluster.host or not self._host_usable(candidate):
                    raise InfraOperationError(
                        f"Host '{spec.host_id}' is not available in the selected cluster.",
                        reason="The host is disconnected, in maintenance mode, or belongs to another cluster.",
                        recommended_action="Select another host or use automatic placement.",
                        retryable=False,
                    )
                host = candidate

            pool = cluster.resourcePool
            if spec.resource_pool_id:
                requested_pool = self._find_by_moref(content, spec.resource_pool_id)
                if requested_pool is not None:
                    pool = requested_pool

            datastores = list(self._cluster_datastores(cluster).values())
            required_bytes = sum(disk.size_gb for disk in spec.disks) * 1024**3
            candidates = [
                datastore for datastore in datastores
                if datastore.summary.accessible and (datastore.summary.freeSpace or 0) >= required_bytes
            ]
            if spec.datastore_id:
                candidates = [datastore for datastore in candidates if datastore._moId == spec.datastore_id]
            if not candidates:
                raise InfraOperationError(
                    "No selected datastore has enough accessible capacity for the blank VM.",
                    reason=f"The VM requires approximately {sum(d.size_gb for d in spec.disks)} GB.",
                    recommended_action="Select another datastore or reduce the requested disk capacity.",
                    retryable=False,
                )
            datastore = max(candidates, key=lambda entry: entry.summary.freeSpace or 0)

            config = vim.vm.ConfigSpec()
            config.name = spec.vm_name
            config.annotation = spec.description
            config.guestId = "otherGuest64"
            config.numCPUs = spec.cpu
            config.memoryMB = spec.memory_mb
            config.files = vim.vm.FileInfo(vmPathName=f"[{datastore.name}]")
            if spec.firmware == FirmwareType.EFI:
                config.firmware = "efi"
                if spec.secure_boot:
                    config.bootOptions = vim.vm.BootOptions(efiSecureBootEnabled=True)

            controller = vim.vm.device.ParaVirtualSCSIController()
            controller.key = -100
            controller.busNumber = 0
            controller.sharedBus = vim.vm.device.VirtualSCSIController.Sharing.noSharing
            controller_spec = vim.vm.device.VirtualDeviceSpec()
            controller_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            controller_spec.device = controller
            device_changes = [controller_spec]

            for index, disk in enumerate(spec.disks):
                backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
                backing.fileName = ""
                backing.diskMode = "persistent"
                backing.thinProvisioned = disk.provisioning.value == "thin"
                virtual_disk = vim.vm.device.VirtualDisk()
                virtual_disk.key = -101 - index
                virtual_disk.controllerKey = controller.key
                # SCSI unit 7 is reserved for the controller itself.
                virtual_disk.unitNumber = index if index < 7 else index + 1
                virtual_disk.capacityInKB = disk.size_gb * 1024 * 1024
                virtual_disk.backing = backing
                disk_spec = vim.vm.device.VirtualDeviceSpec()
                disk_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
                disk_spec.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
                disk_spec.device = virtual_disk
                device_changes.append(disk_spec)
            config.deviceChange = device_changes

            try:
                task = datacenter.vmFolder.CreateVM_Task(config=config, pool=pool, host=host)
                self._wait_for_task(task)
            except Exception as exc:  # noqa: BLE001
                raise _wrap("create_blank_vm", exc) from exc

            created = self._find_vm_by_name(content, spec.vm_name)
            if created is None:
                raise InfraOperationError(
                    f"Create task completed but VM '{spec.vm_name}' was not found.",
                    reason="Inventory inconsistency after VM creation.",
                    recommended_action="Check recent tasks in vCenter before retrying.",
                    retryable=True,
                )
            return VmRef(id=created._moId, name=created.name)

        log.info("vSphere create blank VM: name=%s cluster=%s", spec.vm_name, spec.cluster_id)
        return await self._with_session(target, op)

    async def configure_hardware(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        cpu: int,
        memory_mb: int,
        disks: list,
        firmware: FirmwareType,
        secure_boot: bool,
    ) -> None:
        def op(si):
            content = self._content(si)
            vm = self._find_by_moref(content, vm_id)
            if vm is None:
                raise InfraOperationError(
                    f"VM '{vm_id}' was not found while configuring hardware.",
                    reason="VM removed after clone.",
                    recommended_action="Inspect the job timeline; the VM may need manual cleanup.",
                    retryable=False,
                )

            config = vim.VirtualMachineConfigSpec()
            config.numCPUs = cpu
            config.memoryMB = memory_mb
            if firmware == FirmwareType.EFI:
                config.firmware = "efi"
                config.bootOptions = vim.vm.BootOptions(efiSecureBootEnabled=secure_boot)

            existing_disks = [d for d in vm.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)]
            device_changes: list = []

            # Grow existing disks when the request is larger (never shrink).
            for index, requested in enumerate(disks):
                if index < len(existing_disks):
                    current_kb = existing_disks[index].capacityInKB
                    requested_kb = requested.size_gb * 1024 * 1024
                    if requested_kb > current_kb:
                        disk_spec = vim.vm.device.VirtualDeviceSpec()
                        disk_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
                        disk = existing_disks[index]
                        disk.capacityInKB = requested_kb
                        disk_spec.device = disk
                        device_changes.append(disk_spec)
                else:
                    disk_spec = vim.vm.device.VirtualDeviceSpec()
                    disk_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
                    disk_spec.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
                    disk = vim.vm.device.VirtualDisk()
                    disk.capacityInKB = requested.size_gb * 1024 * 1024
                    disk.unitNumber = len(existing_disks) + index
                    disk.controllerKey = next(
                        (c.key for c in vm.config.hardware.device
                         if isinstance(c, vim.vm.device.VirtualSCSIController)),
                        0,
                    )
                    backing_mode = (
                        vim.vm.device.VirtualDisk.FlatVer2BackingInfo
                    )
                    backing = backing_mode()
                    backing.thinProvisioned = requested.provisioning.value == "thin"
                    if requested.datastore_id:
                        ds = self._find_by_moref(content, requested.datastore_id)
                        if ds is not None:
                            backing.datastore = ds
                    disk.backing = backing
                    disk_spec.device = disk
                    device_changes.append(disk_spec)

            config.deviceChange = device_changes
            try:
                task = vm.ReconfigVM_Task(spec=config)
                self._wait_for_task(task)
            except Exception as exc:  # noqa: BLE001
                raise _wrap("configure_hardware", exc) from exc

        await self._with_session(target, op)

    async def attach_network(
        self, target: VCenterTarget, vm_id: str, network_id: str, adapter_type: AdapterType
    ) -> None:
        def op(si):
            content = self._content(si)
            vm = self._find_by_moref(content, vm_id)
            network = self._find_by_moref(content, network_id)
            if vm is None:
                raise InfraOperationError(
                    f"VM '{vm_id}' was not found while attaching the network adapter.",
                    reason="VM removed after clone.",
                    recommended_action="Inspect the job timeline; the VM may need manual cleanup.",
                    retryable=False,
                )
            if network is None:
                raise InfraOperationError(
                    f"Network '{network_id}' no longer exists on {target.host}.",
                    reason="Port group removed or renamed.",
                    recommended_action="Select a valid port group and retry the network stage.",
                    retryable=True,
                )

            device_changes: list = []
            for device in vm.config.hardware.device:
                if isinstance(device, vim.vm.device.VirtualEthernetCard):
                    remove_spec = vim.vm.device.VirtualDeviceSpec()
                    remove_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.remove
                    remove_spec.device = device
                    device_changes.append(remove_spec)

            if isinstance(network, vim.dvs.DistributedVirtualPortgroup):
                backing = vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo()
                backing.port = vim.dvs.PortConnection()
                backing.port.switchUuid = network.config.distributedVirtualSwitch.uuid
                backing.port.portgroupKey = network.key
            else:
                backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
                backing.deviceName = network.name

            card_cls = (
                vim.vm.device.VirtualVmxnet3
                if adapter_type == AdapterType.VMXNET3
                else vim.vm.device.VirtualE1000e
            )
            card = card_cls()
            card.backing = backing
            card.connectable = vim.vm.device.VirtualDevice.ConnectInfo(
                startConnected=True, allowGuestControl=True, connected=True
            )
            add_spec = vim.vm.device.VirtualDeviceSpec()
            add_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            add_spec.device = card
            device_changes.append(add_spec)

            config = vim.VirtualMachineConfigSpec()
            config.deviceChange = device_changes
            try:
                task = vm.ReconfigVM_Task(spec=config)
                self._wait_for_task(task)
            except Exception as exc:  # noqa: BLE001
                raise _wrap("attach_network", exc) from exc

        await self._with_session(target, op)

    async def power_on(self, target: VCenterTarget, vm_id: str) -> None:
        def op(si):
            content = self._content(si)
            vm = self._find_by_moref(content, vm_id)
            if vm is None:
                raise InfraOperationError(
                    f"VM '{vm_id}' was not found while powering on.",
                    reason="VM removed after clone.",
                    recommended_action="Inspect the job timeline; the VM may need manual cleanup.",
                    retryable=False,
                )
            try:
                task = vm.PowerOnVM_Task()
                self._wait_for_task(task)
            except Exception as exc:  # noqa: BLE001
                raise _wrap("power_on", exc) from exc

        await self._with_session(target, op)

    async def wait_for_tools(self, target: VCenterTarget, vm_id: str, timeout_seconds: float) -> None:
        async def poll() -> None:
            deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=timeout_seconds)
            while dt.datetime.now(dt.timezone.utc) < deadline:
                info = await self.get_vm_info_by_id(target, vm_id)
                if info is not None and info.tools_status in ("toolsOk", "toolsOld"):
                    return
                await asyncio.sleep(_TOOLS_POLL_INTERVAL)
            raise InfraOperationError(
                "VMware Tools did not become ready within the configured timeout.",
                reason=f"Tools heartbeat absent after {int(timeout_seconds)} seconds.",
                recommended_action=(
                    "Verify VMware Tools status in vCenter, then retry the "
                    "'Wait for VMware Tools' stage."
                ),
                technical_detail=f"waited {timeout_seconds}s for toolsOk/toolsOld on {vm_id}",
                retryable=True,
            )

        await poll()

    async def get_vm_info_by_id(self, target: VCenterTarget, vm_id: str) -> PowerStateInfo | None:
        def op(si):
            content = self._content(si)
            vm = self._find_by_moref(content, vm_id)
            if vm is None:
                return None
            guest = vm.guest
            ips: list[str] = []
            if guest.net:
                for nic in guest.net:
                    if nic.ipConfig and nic.ipConfig.ipAddress:
                        ips.extend(ip.ipAddress for ip in nic.ipConfig.ipAddress if ":" not in ip.ipAddress)
            if not ips and guest.ipAddress:
                ips.append(guest.ipAddress)
            return PowerStateInfo(
                power_state=str(vm.runtime.powerState),
                tools_status=str(guest.toolsStatus) if guest.toolsStatus else None,
                ip_addresses=ips,
            )

        return await self._with_session(target, op)
