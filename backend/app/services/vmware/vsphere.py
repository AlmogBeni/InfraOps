"""Production VMware vSphere adapter (pyvmomi).

ENVIRONMENT DEPENDENT — requires a reachable vCenter and the ``pyvmomi``
package. All blocking pyvmomi calls are executed in worker threads; every
failure is translated into an :class:`InfraOperationError` carrying a human
message, a reason, a recommended action and the preserved technical detail.

Credentials are resolved from encrypted backend storage at connect time and are
never logged or exposed. Credential fingerprints invalidate rotated sessions.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import hashlib
import re
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request

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
    IsoImageOut,
    NetworkOut,
    ResourcePoolOut,
)
from app.schemas.provisioning import AdapterType, DiskSpec, FirmwareType
from app.secrets.service import SecretsService
from app.services.vmware.base import (
    OWNER_EXTRA_CONFIG_KEY,
    PowerStateInfo,
    TemporaryMediaRef,
    VCenterTarget,
    VmCreateSpec,
    VmOwnership,
    VmRef,
    VMwareService,
    ensure_tls_policy,
    owner_annotation,
    owner_from_annotation,
    vcenter_ssl_context_or_unverified,
)
from app.services.vmware.inventory_refs import decode_iso_id, encode_iso_id

log = get_logger(__name__)

try:  # pyvmomi is optional — mock deployments never need it.
    from pyVim import connect as pyvim_connect
    from pyVmomi import vim, vmodl

    HAS_PYVMOMI = True
except ImportError:  # pragma: no cover - exercised only without pyvmomi installed
    HAS_PYVMOMI = False

_CONNECTION_TTL_SECONDS = 1800.0
_TOOLS_POLL_INTERVAL = 5.0
_HOST_HTTPS_TIMEOUT_SECONDS = 5.0
# VMware Tools ISO every ESXi host provides (the path VMware's own Packer
# examples for vSphere use).
HOST_TOOLS_ISO_PATH = "[] /vmimages/tools-isoimages/windows.iso"
# Datastore folder for temporary answer media; never offered as installation media.
ANSWER_MEDIA_FOLDER = "infraops-unattend"
_DATASTORE_PATH = re.compile(r"^\[([^\]]+)\] ?(.+)$")
_TASK_POLL_INTERVAL = 0.5

# Set by ``_with_session`` for the duration of one blocking call. ``asyncio``
# copies the current context into ``to_thread`` workers, so the blocking task
# poller sees the event and cancels the vCenter task when the awaiting
# coroutine is cancelled (stage timeout, job cancellation, worker shutdown).
_CANCEL_EVENT: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "infraops_vsphere_cancel_event", default=None
)


class VCenterTaskCancelled(Exception):
    """Raised inside a worker thread after the vCenter task was cancelled."""


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
    if HAS_PYVMOMI and isinstance(exc, vim.fault.InvalidLogin):
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
        self._sessions: dict[str, tuple[object, float, str]] = {}
        self._lock = threading.Lock()

    def get(self, vcenter_id: str, credential_fingerprint: str):
        with self._lock:
            entry = self._sessions.get(vcenter_id)
            if entry is None:
                return None
            instance, created, stored_fingerprint = entry
            if (
                time.monotonic() - created > _CONNECTION_TTL_SECONDS
                or stored_fingerprint != credential_fingerprint
            ):
                del self._sessions[vcenter_id]
                try:
                    pyvim_connect.Disconnect(instance)
                except Exception:  # noqa: BLE001
                    pass
                return None
            return instance

    def put(self, vcenter_id: str, instance, credential_fingerprint: str) -> None:
        with self._lock:
            self._sessions[vcenter_id] = (
                instance,
                time.monotonic(),
                credential_fingerprint,
            )

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
        ensure_tls_policy(target)
        username, password = await self._secrets.get_credentials(
            target.username_secret_ref, target.password_secret_ref
        )
        fingerprint = hashlib.sha256(
            f"{username}\0{password}".encode()
        ).hexdigest()
        cached = self._cache.get(target.id, fingerprint)
        if cached is not None:
            return cached
        try:
            instance = await asyncio.to_thread(self._connect_blocking, target, username, password)
        except InfraOperationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap("connect", exc, retryable=True) from exc
        self._cache.put(target.id, instance, fingerprint)
        return instance

    def _connect_blocking(self, target: VCenterTarget, username: str, password: str):
        context = vcenter_ssl_context_or_unverified(target)
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
        cancel_event = threading.Event()
        token = _CANCEL_EVENT.set(cancel_event)
        try:
            return await asyncio.to_thread(fn, si)
        except asyncio.CancelledError:
            # The coroutine is gone but the thread keeps running; tell it to
            # cancel the vCenter task it is waiting on and stop polling.
            cancel_event.set()
            raise
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
        finally:
            _CANCEL_EVENT.reset(token)

    # ── pyvmomi helpers (blocking, run inside threads) ───────────────────────

    @staticmethod
    def _content(si):
        return si.RetrieveContent()

    @staticmethod
    def _wait_for_task(task) -> None:
        cancel_event = _CANCEL_EVENT.get()
        while task.info.state in (vim.TaskInfo.State.running, vim.TaskInfo.State.queued):
            if cancel_event is not None and cancel_event.is_set():
                try:
                    task.CancelTask()
                    log.warning("Cancelled vCenter task %s after its caller was cancelled", task._moId)
                except Exception:  # noqa: BLE001 - not every task type is cancellable
                    log.warning("vCenter task %s could not be cancelled", getattr(task, "_moId", "?"))
                raise VCenterTaskCancelled(getattr(task, "_moId", "task"))
            time.sleep(_TASK_POLL_INTERVAL)
        if task.info.state == vim.TaskInfo.State.error:
            error = task.info.error
            raise error if error is not None else RuntimeError("Task failed without an error object.")

    @staticmethod
    def _container_view(content, vimtype):
        return content.viewManager.CreateContainerView(content.rootFolder, [vimtype], True)

    @staticmethod
    def _entities_in_container(content, container, vimtypes) -> list:
        """Return recursively discovered entities rooted at one inventory container."""
        requested_types = list(vimtypes) if isinstance(vimtypes, list | tuple) else [vimtypes]
        view = content.viewManager.CreateContainerView(container, requested_types, True)
        try:
            return list(view.view)
        finally:
            view.Destroy()

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
    def _find_vms_by_name(cls, content, name: str) -> list:
        view = cls._container_view(content, vim.VirtualMachine)
        try:
            return [vm for vm in view.view if vm.name.lower() == name.lower()]
        finally:
            view.Destroy()

    @classmethod
    def _find_vm_by_name(cls, content, name: str):
        """Return the single VM with this name; refuse to guess between duplicates."""
        matches = cls._find_vms_by_name(content, name)
        if len(matches) > 1:
            raise InfraOperationError(
                f"More than one virtual machine is named '{name}'.",
                reason="VM names are unique only per folder; InfraOps will not guess which VM to act on.",
                recommended_action="Rename or remove the duplicate VM in vCenter, then retry.",
                technical_detail=", ".join(vm._moId for vm in matches),
                retryable=False,
            )
        return matches[0] if matches else None

    @staticmethod
    def _vm_owner_job_id(vm) -> str | None:
        config = getattr(vm, "config", None)
        for option in getattr(config, "extraConfig", None) or []:
            if getattr(option, "key", None) == OWNER_EXTRA_CONFIG_KEY and option.value:
                return str(option.value)
        return owner_from_annotation(getattr(config, "annotation", None))

    @staticmethod
    def _owner_extra_config(job_id: str) -> list:
        return [vim.option.OptionValue(key=OWNER_EXTRA_CONFIG_KEY, value=job_id)]

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

    @staticmethod
    def _walk_resource_pools(root):
        """Yield a cluster's root resource pool and all descendants."""
        yield root
        for child in getattr(root, "resourcePool", []) or []:
            yield from VsphereVMwareService._walk_resource_pools(child)

    @staticmethod
    def _compute_belongs_to_datacenter(content, compute, datacenter) -> bool:
        entities = VsphereVMwareService._entities_in_container(
            content,
            datacenter.hostFolder,
            (vim.ComputeResource, vim.ClusterComputeResource),
        )
        return any(entry._moId == compute._moId for entry in entities)

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
            if datacenter is None or not isinstance(datacenter, vim.Datacenter):
                raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")
            clusters = []
            entities = self._entities_in_container(
                content,
                datacenter.hostFolder,
                (vim.ComputeResource, vim.ClusterComputeResource),
            )
            seen_ids: set[str] = set()
            for entity in entities:
                if entity._moId in seen_ids:
                    continue
                seen_ids.add(entity._moId)
                hosts = list(entity.host or [])
                drs_config = getattr(
                    getattr(entity, "configurationEx", None),
                    "drsConfig",
                    None,
                )
                clusters.append(
                    ClusterOut(
                        id=entity._moId,
                        name=entity.name,
                        datacenter_id=datacenter_id,
                        drs_enabled=bool(getattr(drs_config, "enabled", False)),
                        hosts_count=len(hosts),
                        total_cpu_cores=sum(
                            int(getattr(getattr(host.hardware, "cpuInfo", None), "numCpuCores", 0) or 0)
                            for host in hosts
                        ),
                        total_memory_gb=round(
                            sum(int(getattr(host.hardware, "memorySize", 0) or 0) for host in hosts)
                            / 1024**3,
                            1,
                        ),
                    )
                )
            return clusters

        return sorted(await self._with_session(target, op), key=lambda c: c.name)

    async def get_hosts(self, target: VCenterTarget, cluster_id: str) -> list[HostOut]:
        def op(si):
            content = self._content(si)
            cluster = self._find_by_moref(content, cluster_id)
            if cluster is None or not isinstance(cluster, vim.ComputeResource):
                raise NotFoundError(f"Compute target '{cluster_id}' does not exist.")
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
            if cluster is None or not isinstance(cluster, vim.ComputeResource):
                raise NotFoundError(f"Compute target '{cluster_id}' does not exist.")
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
            if cluster is None or not isinstance(cluster, vim.ComputeResource):
                raise NotFoundError(f"Compute target '{cluster_id}' does not exist.")
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
            if cluster is None or not isinstance(cluster, vim.ComputeResource):
                raise NotFoundError(f"Compute target '{cluster_id}' does not exist.")
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
                    DatastoreClusterOut(
                        id=pod._moId,
                        name=pod.name,
                        capacity_gb=round(capacity, 1),
                        free_gb=round(free, 1),
                    )
                )
            return pods

        return await self._with_session(target, op)

    async def get_networks(self, target: VCenterTarget, datacenter_id: str) -> list[NetworkOut]:
        def op(si):
            content = self._content(si)
            datacenter = self._find_by_moref(content, datacenter_id)
            if datacenter is None or not isinstance(datacenter, vim.Datacenter):
                raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")
            networks = []
            view = content.viewManager.CreateContainerView(
                datacenter.networkFolder, [vim.Network], True
            )
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

    async def get_isos(self, target: VCenterTarget, datacenter_id: str) -> list[IsoImageOut]:
        def op(si):
            content = self._content(si)
            datacenter = self._find_by_moref(content, datacenter_id)
            if datacenter is None or not isinstance(datacenter, vim.Datacenter):
                raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")

            view = content.viewManager.CreateContainerView(
                datacenter.datastoreFolder, [vim.Datastore], True
            )
            try:
                datastores = list(view.view)
            finally:
                view.Destroy()

            searches: list[tuple[object, object]] = []
            for datastore in datastores:
                if not bool(getattr(datastore.summary, "accessible", False)):
                    continue
                search_spec = vim.HostDatastoreBrowser.SearchSpec()
                search_spec.matchPattern = ["*.iso", "*.ISO"]
                details = vim.HostDatastoreBrowser.FileInfo.Details()
                details.fileSize = True
                details.modification = True
                search_spec.details = details
                task = datastore.browser.SearchDatastoreSubFolders_Task(
                    datastorePath=f"[{datastore.name}]",
                    searchSpec=search_spec,
                )
                searches.append((datastore, task))

            # Datastore-browser searches are independent vCenter tasks. Start
            # every accessible search first so vCenter can execute them in
            # parallel, then collect their results in a deterministic order.
            results: list[IsoImageOut] = []
            for datastore, task in searches:
                self._wait_for_task(task)
                for folder in task.info.result or []:
                    folder_path = str(folder.folderPath or f"[{datastore.name}]")
                    for entry in folder.file or []:
                        relative_path = str(entry.path or "")
                        if not relative_path.lower().endswith(".iso"):
                            continue
                        separator = "" if folder_path.endswith(("/", " ")) else " "
                        full_path = f"{folder_path}{separator}{relative_path}"
                        if f"{ANSWER_MEDIA_FOLDER}/" in full_path:
                            continue  # InfraOps' own temporary answer media
                        name = relative_path.rstrip("/").rsplit("/", 1)[-1]
                        results.append(
                            IsoImageOut(
                                id=encode_iso_id(datastore._moId, full_path),
                                name=name,
                                datacenter_id=datacenter_id,
                                datacenter_name=datacenter.name,
                                datastore_id=datastore._moId,
                                datastore_name=datastore.name,
                                path=full_path,
                                size_bytes=getattr(entry, "fileSize", None),
                                last_modified=getattr(entry, "modification", None),
                            )
                        )
            return results

        return sorted(
            await self._with_session(target, op, operation="list-isos"),
            key=lambda image: (image.datastore_name.casefold(), image.name.casefold()),
        )

    @staticmethod
    def _datacenter_path(content, datacenter) -> str:
        """Inventory path of a datacenter (``Folder/DC``) for datastore file URLs."""
        names: list[str] = []
        entity = datacenter
        while entity is not None and entity != content.rootFolder:
            names.append(entity.name)
            entity = getattr(entity, "parent", None)
        return "/".join(reversed(names))

    def _datastore_file_url(self, target: VCenterTarget, content, datacenter, datastore_path: str) -> str:
        match = _DATASTORE_PATH.fullmatch(datastore_path)
        if match is None:
            raise InfraOperationError(
                f"'{datastore_path}' is not a datastore path.",
                reason="Datastore paths have the form '[datastore] folder/file'.",
                recommended_action="Report this incident to the platform administrators.",
                retryable=False,
            )
        datastore_name, relative = match.group(1), match.group(2)
        query = urllib.parse.urlencode(
            {"dcPath": self._datacenter_path(content, datacenter), "dsName": datastore_name}
        )
        return (
            f"https://{target.host}:{target.port}/folder/"
            f"{urllib.parse.quote(relative, safe='/')}?{query}"
        )

    async def read_datastore_file(
        self,
        target: VCenterTarget,
        datacenter_id: str,
        datastore_path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        def op(si):
            content = self._content(si)
            datacenter = self._find_by_moref(content, datacenter_id)
            if datacenter is None or not isinstance(datacenter, vim.Datacenter):
                raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")
            request = urllib.request.Request(
                self._datastore_file_url(target, content, datacenter, datastore_path), method="GET"
            )
            # Servers that ignore Range still stream the file; only the first
            # max_bytes are read before the connection is closed.
            request.add_header("Range", f"bytes=0-{max_bytes - 1}")
            cookie = getattr(getattr(si, "_stub", None), "cookie", None)
            if cookie:
                request.add_header("Cookie", cookie)
            context = vcenter_ssl_context_or_unverified(target)
            with urllib.request.urlopen(request, context=context, timeout=120) as response:  # noqa: S310
                if response.status not in (200, 206):
                    raise RuntimeError(f"datastore download returned HTTP {response.status}")
                return response.read(max_bytes)

        try:
            return await self._with_session(target, op, operation="read-datastore-file")
        except NotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap("read-datastore-file", exc) from exc

    # ── Inventory queries ────────────────────────────────────────────────────

    async def vm_exists(self, target: VCenterTarget, vm_name: str) -> bool:
        def op(si):
            return bool(self._find_vms_by_name(self._content(si), vm_name))

        return await self._with_session(target, op)

    @staticmethod
    def _power_state_info(vm) -> PowerStateInfo:
        guest = vm.guest
        ips: list[str] = []
        if guest.net:
            for nic in guest.net:
                if nic.ipConfig and nic.ipConfig.ipAddress:
                    ips.extend(ip.ipAddress for ip in nic.ipConfig.ipAddress if ":" not in ip.ipAddress)
        if not ips and guest.ipAddress:
            ips.append(guest.ipAddress)

        def text(owner, name: str) -> str | None:
            value = getattr(owner, name, None)
            return str(value) if value else None

        return PowerStateInfo(
            power_state=str(vm.runtime.powerState),
            tools_status=text(guest, "toolsStatus"),
            tools_running_status=text(guest, "toolsRunningStatus"),
            tools_version_status=text(guest, "toolsVersionStatus2"),
            guest_state=text(guest, "guestState"),
            guest_operations_ready=bool(getattr(guest, "guestOperationsReady", False)),
            guest_family=text(guest, "guestFamily"),
            ip_addresses=ips,
            host_id=vm.runtime.host._moId if vm.runtime.host else None,
            guest_host_name=text(guest, "hostName"),
        )

    async def get_vm_info(self, target: VCenterTarget, vm_name: str) -> PowerStateInfo | None:
        def op(si):
            vm = self._find_vm_by_name(self._content(si), vm_name)
            return None if vm is None else self._power_state_info(vm)

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

    async def find_vm_ownership(self, target: VCenterTarget, vm_name: str) -> VmOwnership | None:
        def op(si):
            vm = self._find_vm_by_name(self._content(si), vm_name)
            if vm is None:
                return None
            return VmOwnership(vm_id=vm._moId, name=vm.name, owner_job_id=self._vm_owner_job_id(vm))

        return await self._with_session(target, op, operation="find-vm-ownership")

    async def tag_vm_owner(self, target: VCenterTarget, vm_id: str, job_id: str) -> None:
        def op(si):
            vm = self._find_by_moref(self._content(si), vm_id)
            if vm is None:
                raise InfraOperationError(
                    f"VM '{vm_id}' was not found while recording its InfraOps owner.",
                    reason="VM removed immediately after creation.",
                    recommended_action="Inspect recent vCenter tasks before retrying.",
                    retryable=True,
                )
            existing = self._vm_owner_job_id(vm)
            if existing is not None and existing != job_id:
                raise InfraOperationError(
                    f"VM '{vm.name}' belongs to another InfraOps job.",
                    reason=f"Ownership marker {existing!r} does not match this job.",
                    recommended_action="Choose a different VM name.",
                    retryable=False,
                )
            self._wait_for_task(
                vm.ReconfigVM_Task(spec=vim.vm.ConfigSpec(extraConfig=self._owner_extra_config(job_id)))
            )

        await self._with_session(target, op, operation="tag-vm-owner")

    # ── Lifecycle operations ─────────────────────────────────────────────────

    async def create_vm(self, target: VCenterTarget, spec: VmCreateSpec) -> VmRef:
        try:
            iso_datastore_id, iso_path = decode_iso_id(spec.iso_id)
        except ValueError as exc:
            raise InfraOperationError(
                "The selected ISO identifier is invalid.",
                reason=str(exc),
                recommended_action="Refresh the ISO inventory and select the image again.",
                retryable=False,
            ) from exc

        def op(si):
            content = self._content(si)
            if self._find_vms_by_name(content, spec.vm_name):
                raise InfraOperationError(
                    f"A virtual machine named '{spec.vm_name}' already exists.",
                    reason="Duplicate VM name in the vCenter inventory.",
                    recommended_action="Choose a different VM name and resubmit the request.",
                    retryable=False,
                )

            datacenter = self._find_by_moref(content, spec.datacenter_id)
            cluster = self._find_by_moref(content, spec.cluster_id)
            if (
                datacenter is None
                or not isinstance(datacenter, vim.Datacenter)
                or cluster is None
                or not isinstance(cluster, vim.ComputeResource)
                or not self._compute_belongs_to_datacenter(content, cluster, datacenter)
            ):
                raise InfraOperationError(
                    "The selected datacenter or compute target was not found.",
                    reason="The placement inventory changed after validation.",
                    recommended_action="Re-open the wizard and select the infrastructure again.",
                    retryable=False,
                )

            host = None
            if spec.host_id:
                candidate = self._find_by_moref(content, spec.host_id)
                if candidate is None or candidate not in cluster.host or not self._host_usable(candidate):
                    raise InfraOperationError(
                        f"Host '{spec.host_id}' is not available in the selected compute target.",
                        reason="The host is disconnected, in maintenance mode, or belongs to another compute target.",
                        recommended_action="Select another host or use automatic placement.",
                        retryable=False,
                    )
                host = candidate

            pool = cluster.resourcePool
            if spec.resource_pool_id:
                requested_pool = self._find_by_moref(content, spec.resource_pool_id)
                valid_pool_ids = {
                    entry._moId for entry in self._walk_resource_pools(cluster.resourcePool)
                }
                if requested_pool is None or requested_pool._moId not in valid_pool_ids:
                    raise InfraOperationError(
                        f"Resource pool '{spec.resource_pool_id}' was not found in the compute target.",
                        reason="Resource pool removed or moved after validation.",
                        recommended_action="Re-select the placement target and retry.",
                        retryable=False,
                    )
                pool = requested_pool

            datastores = list(self._cluster_datastores(cluster).values())
            required_bytes = spec.os_disk.size_gb * 1024**3
            candidates = [
                datastore for datastore in datastores
                if datastore.summary.accessible and (datastore.summary.freeSpace or 0) >= required_bytes
            ]
            if spec.datastore_id:
                candidates = [datastore for datastore in candidates if datastore._moId == spec.datastore_id]
            if not candidates:
                raise InfraOperationError(
                    "No selected datastore has enough accessible capacity for the VM.",
                    reason=f"The OS disk requires approximately {spec.os_disk.size_gb} GB.",
                    recommended_action="Select another datastore or reduce the requested disk capacity.",
                    retryable=False,
                )
            datastore = max(candidates, key=lambda entry: entry.summary.freeSpace or 0)

            cluster_datastore_ids = {entry._moId for entry in datastores}
            datastore_view = content.viewManager.CreateContainerView(
                datacenter.datastoreFolder, [vim.Datastore], True
            )
            try:
                iso_datastore = next(
                    (entry for entry in datastore_view.view if entry._moId == iso_datastore_id),
                    None,
                )
            finally:
                datastore_view.Destroy()
            expected_prefix = f"[{getattr(iso_datastore, 'name', '')}] "
            if (
                iso_datastore is None
                or iso_datastore_id not in cluster_datastore_ids
                or not bool(getattr(iso_datastore.summary, "accessible", False))
                or not iso_path.startswith(expected_prefix)
                or not iso_path.lower().endswith(".iso")
            ):
                raise InfraOperationError(
                    "The selected ISO is not available in the selected datacenter.",
                    reason="Its datastore is missing, inaccessible, or does not match the ISO path.",
                    recommended_action="Refresh the ISO inventory and select another image.",
                    retryable=False,
                )

            config = vim.vm.ConfigSpec()
            config.name = spec.vm_name
            config.annotation = owner_annotation(spec.description, spec.job_id)
            if spec.job_id:
                config.extraConfig = self._owner_extra_config(spec.job_id)
            config.guestId = "windows9Server64Guest"
            config.numCPUs = spec.cpu
            config.memoryMB = spec.memory_mb
            config.files = vim.vm.FileInfo(vmPathName=f"[{datastore.name}]")
            if spec.firmware == FirmwareType.EFI:
                config.firmware = "efi"
                if spec.secure_boot:
                    config.bootOptions = vim.vm.BootOptions(efiSecureBootEnabled=True)

            # LSI Logic SAS: Windows Setup ships its driver, so the OS disk is
            # visible during installation under BIOS and EFI without injecting
            # drivers. Data disks are hot-added to it once Windows is running.
            controller = vim.vm.device.VirtualLsiLogicSASController()
            controller.key = -100
            controller.busNumber = 0
            controller.sharedBus = vim.vm.device.VirtualSCSIController.Sharing.noSharing
            controller_spec = vim.vm.device.VirtualDeviceSpec()
            controller_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            controller_spec.device = controller
            device_changes = [controller_spec]

            # Only the OS disk exists during Setup, so it is always disk 0.
            backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
            backing.fileName = ""
            backing.diskMode = "persistent"
            backing.thinProvisioned = spec.os_disk.provisioning.value == "thin"
            os_disk = vim.vm.device.VirtualDisk()
            os_disk.key = -101
            os_disk.controllerKey = controller.key
            os_disk.unitNumber = 0
            os_disk.capacityInKB = spec.os_disk.size_gb * 1024 * 1024
            os_disk.backing = backing
            disk_spec = vim.vm.device.VirtualDeviceSpec()
            disk_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            disk_spec.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
            disk_spec.device = os_disk
            device_changes.append(disk_spec)

            sata = vim.vm.device.VirtualAHCIController()
            sata.key = -200
            sata.busNumber = 0
            sata_spec = vim.vm.device.VirtualDeviceSpec()
            sata_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            sata_spec.device = sata
            device_changes.append(sata_spec)

            # SATA 0:0 boots Windows Setup; SATA 0:1 is the host's VMware Tools
            # ISO, installed by the answer file's first-logon command.
            for key, unit, iso_backing in (
                (-201, 0, vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=iso_path, datastore=iso_datastore)),
                (-202, 1, vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=HOST_TOOLS_ISO_PATH)),
            ):
                cdrom = vim.vm.device.VirtualCdrom()
                cdrom.key = key
                cdrom.controllerKey = sata.key
                cdrom.unitNumber = unit
                cdrom.backing = iso_backing
                cdrom.connectable = vim.vm.device.VirtualDevice.ConnectInfo(
                    startConnected=True,
                    allowGuestControl=True,
                    connected=True,
                )
                cdrom_spec = vim.vm.device.VirtualDeviceSpec()
                cdrom_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
                cdrom_spec.device = cdrom
                device_changes.append(cdrom_spec)
            config.deviceChange = device_changes

            try:
                task = datacenter.vmFolder.CreateVM_Task(config=config, pool=pool, host=host)
                self._wait_for_task(task)
            except Exception as exc:  # noqa: BLE001
                raise _wrap("create_vm", exc) from exc

            # Use the task result (the new VM's moref), never a name lookup
            # that could match an unrelated VM with the same name.
            created = task.info.result
            if created is None:
                raise InfraOperationError(
                    f"Create task completed but VM '{spec.vm_name}' was not found.",
                    reason="Inventory inconsistency after VM creation.",
                    recommended_action="Check recent tasks in vCenter before retrying.",
                    retryable=True,
                )
            boot_order = [vim.vm.BootOptions.BootableCdromDevice()]
            created_disks = [
                device
                for device in created.config.hardware.device
                if isinstance(device, vim.vm.device.VirtualDisk)
            ]
            if created_disks:
                boot_order.append(vim.vm.BootOptions.BootableDiskDevice(deviceKey=created_disks[0].key))
            boot_options = vim.vm.BootOptions(
                bootOrder=boot_order,
                bootRetryEnabled=True,
                bootRetryDelay=10_000,
            )
            if spec.firmware == FirmwareType.EFI:
                boot_options.efiSecureBootEnabled = spec.secure_boot
            try:
                task = created.ReconfigVM_Task(spec=vim.vm.ConfigSpec(bootOptions=boot_options))
                self._wait_for_task(task)
            except Exception as exc:  # noqa: BLE001
                raise _wrap("configure_installation_boot", exc) from exc
            return VmRef(id=created._moId, name=created.name)

        log.info("vSphere create VM: name=%s cluster=%s", spec.vm_name, spec.cluster_id)
        return await self._with_session(target, op)

    async def configure_hardware(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        cpu: int,
        memory_mb: int,
    ) -> None:
        def op(si):
            vm = self._find_by_moref(self._content(si), vm_id)
            if vm is None:
                raise InfraOperationError(
                    f"VM '{vm_id}' was not found while configuring hardware.",
                    reason="VM removed after creation.",
                    recommended_action="Inspect the job timeline; the VM may need manual cleanup.",
                    retryable=False,
                )
            if vm.config.hardware.numCPU == cpu and vm.config.hardware.memoryMB == memory_mb:
                return
            try:
                self._wait_for_task(
                    vm.ReconfigVM_Task(spec=vim.VirtualMachineConfigSpec(numCPUs=cpu, memoryMB=memory_mb))
                )
            except Exception as exc:  # noqa: BLE001
                raise _wrap("configure_hardware", exc) from exc

        await self._with_session(target, op)

    async def attach_network(
        self,
        target: VCenterTarget,
        vm_id: str,
        network_id: str,
        adapter_type: AdapterType,
        datacenter_id: str,
    ) -> None:
        def op(si):
            content = self._content(si)
            vm = self._find_by_moref(content, vm_id)
            network = None
            datacenter = self._find_by_moref(content, datacenter_id)
            if datacenter is None or not isinstance(datacenter, vim.Datacenter):
                raise InfraOperationError(
                    f"Datacenter '{datacenter_id}' was not found while attaching the network.",
                    reason="The placement inventory changed after validation.",
                    recommended_action="Refresh the infrastructure inventory and retry.",
                    retryable=False,
                )
            vm_datacenter = self._owning_datacenter(vm) if vm is not None else None
            if vm_datacenter is not None and vm_datacenter._moId != datacenter_id:
                raise InfraOperationError(
                    "The VM and selected network datacenter do not match.",
                    reason="The VM is outside the requested datacenter boundary.",
                    recommended_action="Select a network from the VM's datacenter.",
                    retryable=False,
                )
            view = content.viewManager.CreateContainerView(
                datacenter.networkFolder, [vim.Network], True
            )
            try:
                network = next(
                    (entry for entry in view.view if entry._moId == network_id), None
                )
            finally:
                view.Destroy()
            if vm is None:
                raise InfraOperationError(
                    f"VM '{vm_id}' was not found while attaching the network adapter.",
                    reason="VM removed after creation.",
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
                    reason="VM removed after creation.",
                    recommended_action="Inspect the job timeline; the VM may need manual cleanup.",
                    retryable=False,
                )
            try:
                task = vm.PowerOnVM_Task()
                self._wait_for_task(task)
            except Exception as exc:  # noqa: BLE001
                raise _wrap("power_on", exc) from exc

        await self._with_session(target, op)

    async def reset(self, target: VCenterTarget, vm_id: str) -> None:
        def op(si):
            vm = self._find_by_moref(self._content(si), vm_id)
            if vm is None:
                raise InfraOperationError(
                    f"VM '{vm_id}' was not found while resetting it.",
                    reason="VM removed after creation.",
                    recommended_action="Inspect the job timeline; the VM may need manual cleanup.",
                    retryable=False,
                )
            try:
                self._wait_for_task(vm.ResetVM_Task())
            except Exception as exc:  # noqa: BLE001
                raise _wrap("reset", exc) from exc

        await self._with_session(target, op, operation="reset")

    async def send_keystrokes(self, target: VCenterTarget, vm_id: str, usb_hid_usages: list[int]) -> int:
        def op(si):
            vm = self._find_by_moref(self._content(si), vm_id)
            if vm is None:
                raise InfraOperationError(
                    f"VM '{vm_id}' was not found while sending keystrokes.",
                    reason="VM removed after creation.",
                    recommended_action="Inspect the job timeline; the VM may need manual cleanup.",
                    retryable=False,
                )
            # Keyboard usage page (0x07) in the low 16 bits, as govc/Packer send it.
            events = [vim.UsbScanCodeSpec.KeyEvent(usbHidCode=(usage << 16) | 0x07) for usage in usb_hid_usages]
            return int(vm.PutUsbScanCodes(vim.UsbScanCodeSpec(keyEvents=events)) or 0)

        return await self._with_session(target, op, operation="send-keystrokes")

    def _missing_vm(self, vm_id: str, action: str) -> InfraOperationError:
        return InfraOperationError(
            f"VM '{vm_id}' was not found while {action}.",
            reason="VM removed after creation.",
            recommended_action="Inspect the job timeline; the VM may need manual cleanup.",
            retryable=False,
        )

    def _reconfigure_answering_questions(self, vm, spec, operation: str) -> None:
        """Reconfigure ``vm``, answering the CD-ROM door-lock question if it appears.

        Disconnecting a CD the guest has locked raises a VM question that blocks
        the task until answered; the only correct answer for unattended
        provisioning is to override the lock.
        """
        try:
            task = vm.ReconfigVM_Task(spec=spec)
            deadline = time.monotonic() + 300
            while task.info.state in (vim.TaskInfo.State.running, vim.TaskInfo.State.queued):
                question = getattr(getattr(vm, "runtime", None), "question", None)
                if question is not None:
                    choices = list(getattr(getattr(question, "choice", None), "choiceInfo", None) or [])
                    override = next(
                        (choice for choice in choices if str(getattr(choice, "label", "")).lower() == "yes"),
                        None,
                    )
                    if override is not None:
                        vm.AnswerVM(questionId=question.id, answerChoice=override.key)
                        log.info("Answered VM question %s on %s to release a locked CD-ROM", question.id, vm._moId)
                if time.monotonic() > deadline:
                    raise RuntimeError("Reconfiguration did not finish within 300 seconds.")
                time.sleep(_TASK_POLL_INTERVAL)
            self._wait_for_task(task)
        except Exception as exc:  # noqa: BLE001
            raise _wrap(operation, exc) from exc

    @staticmethod
    def _free_unit(controller, devices, *, reserved: frozenset[int] = frozenset()) -> int | None:
        used = {
            device.unitNumber for device in devices
            if getattr(device, "controllerKey", None) == controller.key and device.unitNumber is not None
        }
        limit = 30 if isinstance(controller, vim.vm.device.VirtualAHCIController) else 16
        return next((unit for unit in range(limit) if unit not in used and unit not in reserved), None)

    async def attach_answer_media(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        datacenter_id: str,
        datastore_id: str | None,
        file_name: str,
        content: bytes,
    ) -> TemporaryMediaRef:
        safe_name = file_name.replace("/", "_").replace("\\", "_")

        def op(si):
            inventory = self._content(si)
            vm = self._find_by_moref(inventory, vm_id)
            datacenter = self._find_by_moref(inventory, datacenter_id)
            if vm is None or datacenter is None or not isinstance(datacenter, vim.Datacenter):
                raise InfraOperationError(
                    "The VM or datacenter disappeared before the answer media could be attached.",
                    reason="vCenter inventory changed after validation.",
                    recommended_action="Refresh inventory and retry the preparation stage.",
                    retryable=True,
                )
            vm_datastores = list(getattr(vm, "datastore", []) or [])
            datastore = next(
                (entry for entry in vm_datastores if not datastore_id or entry._moId == datastore_id),
                None,
            )
            if datastore is None:
                raise InfraOperationError(
                    "No VM datastore is available for the temporary answer media.",
                    reason="The selected datastore is not attached to the VM.",
                    recommended_action="Select a datastore accessible to the VM and retry.",
                    retryable=False,
                )

            folder = f"[{datastore.name}] {ANSWER_MEDIA_FOLDER}"
            datastore_path = f"{folder}/{safe_name}"
            try:
                inventory.fileManager.MakeDirectory(
                    name=folder,
                    datacenter=datacenter,
                    createParentDirectories=True,
                )
            except vim.fault.FileAlreadyExists:
                pass

            request = urllib.request.Request(
                self._datastore_file_url(target, inventory, datacenter, datastore_path),
                data=content,
                method="PUT",
            )
            request.add_header("Content-Type", "application/octet-stream")
            cookie = getattr(getattr(si, "_stub", None), "cookie", None)
            if cookie:
                request.add_header("Cookie", cookie)
            context = vcenter_ssl_context_or_unverified(target)
            with urllib.request.urlopen(request, context=context, timeout=120) as response:  # noqa: S310
                if response.status not in (200, 201):
                    raise RuntimeError(f"datastore upload returned HTTP {response.status}")

            devices = list(vm.config.hardware.device)
            existing = next(
                (
                    device for device in devices
                    if isinstance(device, vim.vm.device.VirtualCdrom)
                    and getattr(getattr(device, "backing", None), "fileName", None) == datastore_path
                ),
                None,
            )
            if existing is not None:
                return TemporaryMediaRef(datastore_path=datastore_path)
            sata = next(
                (device for device in devices if isinstance(device, vim.vm.device.VirtualAHCIController)),
                None,
            )
            unit = self._free_unit(sata, devices) if sata is not None else None
            if sata is None or unit is None:
                raise InfraOperationError(
                    "The VM has no free SATA slot for the answer media.",
                    reason="The SATA controller created with the VM is missing or full.",
                    recommended_action="Delete the VM and submit a new request.",
                    retryable=False,
                )
            # A read-only CD is "removable read-only media" to Windows Setup,
            # which reads Autounattend.xml from its root under BIOS and EFI.
            cdrom = vim.vm.device.VirtualCdrom(
                key=-291,
                controllerKey=sata.key,
                unitNumber=unit,
                backing=vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=datastore_path, datastore=datastore),
                connectable=vim.vm.device.VirtualDevice.ConnectInfo(
                    startConnected=True,
                    allowGuestControl=False,
                    connected=True,
                ),
            )
            change = vim.vm.device.VirtualDeviceSpec()
            change.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            change.device = cdrom
            self._wait_for_task(vm.ReconfigVM_Task(spec=vim.vm.ConfigSpec(deviceChange=[change])))
            return TemporaryMediaRef(datastore_path=datastore_path)

        try:
            return await self._with_session(target, op, operation="attach-answer-media")
        except Exception as exc:  # noqa: BLE001
            raise _wrap("attach-answer-media", exc) from exc

    async def remove_answer_media(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        datacenter_id: str,
        datastore_path: str,
    ) -> None:
        def op(si):
            inventory = self._content(si)
            vm = self._find_by_moref(inventory, vm_id)
            datacenter = self._find_by_moref(inventory, datacenter_id)
            if vm is not None:
                changes = []
                for device in vm.config.hardware.device:
                    if (
                        isinstance(device, vim.vm.device.VirtualCdrom)
                        and getattr(getattr(device, "backing", None), "fileName", None) == datastore_path
                    ):
                        change = vim.vm.device.VirtualDeviceSpec()
                        change.operation = vim.vm.device.VirtualDeviceSpec.Operation.remove
                        change.device = device
                        changes.append(change)
                if changes:
                    self._reconfigure_answering_questions(
                        vm, vim.vm.ConfigSpec(deviceChange=changes), "remove-answer-media"
                    )
            try:
                task = inventory.fileManager.DeleteDatastoreFile_Task(
                    name=datastore_path,
                    datacenter=datacenter,
                )
                self._wait_for_task(task)
            except vim.fault.FileNotFound:
                pass

        await self._with_session(target, op, operation="remove-answer-media")

    async def wait_for_tools(self, target: VCenterTarget, vm_id: str, timeout_seconds: float) -> None:
        deadline = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=timeout_seconds)
        while dt.datetime.now(dt.UTC) < deadline:
            info = await self.get_vm_info_by_id(target, vm_id)
            if (
                info is not None
                and (
                    info.tools_status in ("toolsOk", "toolsOld")
                    or info.tools_running_status == "guestToolsRunning"
                )
                and info.guest_operations_ready
            ):
                return
            await asyncio.sleep(_TOOLS_POLL_INTERVAL)
        raise InfraOperationError(
            "VMware Tools did not become ready within the configured timeout.",
            reason=f"Tools heartbeat absent after {int(timeout_seconds)} seconds.",
            recommended_action=(
                "Open the console screenshot attached to this step to see where the guest stopped, "
                "then retry the failed stage."
            ),
            technical_detail=f"waited {timeout_seconds}s for toolsOk/toolsOld on {vm_id}",
            retryable=True,
        )

    async def detach_installation_media(self, target: VCenterTarget, vm_id: str) -> list[str]:
        def op(si):
            vm = self._find_by_moref(self._content(si), vm_id)
            if vm is None:
                raise self._missing_vm(vm_id, "removing installation media")
            detached: list[str] = []
            changes = []
            for device in vm.config.hardware.device:
                if not isinstance(device, vim.vm.device.VirtualCdrom):
                    continue
                file_name = getattr(getattr(device, "backing", None), "fileName", None)
                if not isinstance(device.backing, vim.vm.device.VirtualCdrom.IsoBackingInfo):
                    continue
                if f"{ANSWER_MEDIA_FOLDER}/" in str(file_name or ""):
                    continue  # removed with its datastore file by remove_answer_media
                device.backing = vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo(
                    deviceName="", exclusive=False
                )
                device.connectable = vim.vm.device.VirtualDevice.ConnectInfo(
                    startConnected=False, allowGuestControl=True, connected=False
                )
                change = vim.vm.device.VirtualDeviceSpec()
                change.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
                change.device = device
                changes.append(change)
                detached.append(str(file_name or ""))
            disks = [
                device for device in vm.config.hardware.device
                if isinstance(device, vim.vm.device.VirtualDisk)
            ]
            spec = vim.vm.ConfigSpec(deviceChange=changes)
            if disks:
                # Windows is installed; never boot installation media again.
                spec.bootOptions = vim.vm.BootOptions(
                    bootOrder=[vim.vm.BootOptions.BootableDiskDevice(deviceKey=disks[0].key)]
                )
            if changes or disks:
                self._reconfigure_answering_questions(vm, spec, "detach-installation-media")
            return detached

        return await self._with_session(target, op, operation="detach-installation-media")

    async def add_data_disks(self, target: VCenterTarget, vm_id: str, disks: list[DiskSpec]) -> int:
        def op(si):
            vm = self._find_by_moref(self._content(si), vm_id)
            if vm is None:
                raise self._missing_vm(vm_id, "adding data disks")
            devices = list(vm.config.hardware.device)
            existing = [device for device in devices if isinstance(device, vim.vm.device.VirtualDisk)]
            # The OS disk is the first; anything beyond it was added by an
            # earlier attempt of this stage, so a retry only adds the rest.
            missing = disks[max(len(existing) - 1, 0):]
            if not missing:
                return 0
            controller = next(
                (device for device in devices if isinstance(device, vim.vm.device.VirtualLsiLogicSASController)),
                None,
            )
            if controller is None:
                raise InfraOperationError(
                    "The VM's disk controller was not found.",
                    reason="The LSI Logic SAS controller created with the VM is missing.",
                    recommended_action="Delete the VM and submit a new request.",
                    retryable=False,
                )
            changes = []
            pending = list(devices)
            for index, disk in enumerate(missing):
                # Unit 7 is the SCSI controller itself.
                unit = self._free_unit(controller, pending, reserved=frozenset({7}))
                if unit is None:
                    raise InfraOperationError(
                        "The VM's disk controller has no free slot for another disk.",
                        reason="All 15 disk slots on SCSI controller 0 are in use.",
                        recommended_action="Request fewer data disks.",
                        retryable=False,
                    )
                backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
                backing.fileName = ""
                backing.diskMode = "persistent"
                backing.thinProvisioned = disk.provisioning.value == "thin"
                device = vim.vm.device.VirtualDisk()
                device.key = -300 - index
                device.controllerKey = controller.key
                device.unitNumber = unit
                device.capacityInKB = disk.size_gb * 1024 * 1024
                device.backing = backing
                change = vim.vm.device.VirtualDeviceSpec()
                change.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
                change.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
                change.device = device
                changes.append(change)
                pending.append(device)
            try:
                self._wait_for_task(vm.ReconfigVM_Task(spec=vim.vm.ConfigSpec(deviceChange=changes)))
            except Exception as exc:  # noqa: BLE001
                raise _wrap("add_data_disks", exc) from exc
            return len(changes)

        return await self._with_session(target, op, operation="add-data-disks")

    async def check_host_https(self, target: VCenterTarget, host_names: list[str]) -> dict[str, str | None]:
        context = vcenter_ssl_context_or_unverified(target)
        verify = context.verify_mode != ssl.CERT_NONE

        async def probe(name: str) -> str | None:
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        name, 443, ssl=context, server_hostname=name if verify else None
                    ),
                    timeout=_HOST_HTTPS_TIMEOUT_SECONDS,
                )
            except ssl.SSLCertVerificationError as exc:
                return f"certificate not trusted ({getattr(exc, 'verify_message', None) or exc})"
            except TimeoutError:
                return f"no answer on TCP 443 within {int(_HOST_HTTPS_TIMEOUT_SECONDS)} seconds"
            except socket.gaierror:
                return "the name does not resolve"
            except OSError as exc:
                return f"connection failed ({exc.strerror or exc})"
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass
            return None

        results = await asyncio.gather(*(probe(name) for name in host_names))
        return dict(zip(host_names, results, strict=True))

    async def capture_screenshot(self, target: VCenterTarget, vm_id: str) -> str:
        def op(si):
            vm = self._find_by_moref(self._content(si), vm_id)
            if vm is None:
                raise self._missing_vm(vm_id, "capturing a console screenshot")
            try:
                task = vm.CreateScreenshot_Task()
                self._wait_for_task(task)
            except Exception as exc:  # noqa: BLE001
                raise _wrap("capture_screenshot", exc) from exc
            return str(task.info.result or "")

        return await self._with_session(target, op, operation="capture-screenshot")

    async def get_vm_info_by_id(self, target: VCenterTarget, vm_id: str) -> PowerStateInfo | None:
        def op(si):
            vm = self._find_by_moref(self._content(si), vm_id)
            return None if vm is None else self._power_state_info(vm)

        return await self._with_session(target, op)
