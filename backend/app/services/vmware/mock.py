"""Fully functional in-memory VMware simulation (``INFRASTRUCTURE_MODE=mock``).

Simulates a realistic two-datacenter enterprise estate so the complete
provisioning workflow can be demonstrated without any vCenter. State is kept
per registered vCenter id and survives across requests within the process;
created VMs consume datastore capacity and register their IPs exactly like the
real adapter would report them. Windows Setup "completes" a few seconds after
a VM with its answer media attached is powered on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

from app.core.errors import InfraOperationError, NotFoundError, ServiceUnavailableError
from app.core.logging import get_logger
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
from app.schemas.provisioning import AdapterType, DiskSpec
from app.services.vmware.base import (
    PowerStateInfo,
    TemporaryMediaRef,
    VCenterTarget,
    VmCreateSpec,
    VmOwnership,
    VmRef,
    VMwareService,
)

log = get_logger(__name__)

# Simulated latencies (seconds) — enough to visualise progress, fast for demos.
_LATENCY_DISCOVERY = 0.15
_LATENCY_CREATE = 4.0
_LATENCY_RECONFIG = 1.0
_LATENCY_POWER_ON = 1.5
_TOOLS_READY_DELAY_SECONDS = 3.0

# The canonical mock vCenter id used by seed data.
DEFAULT_MOCK_VCENTER_ID = "11111111-1111-4111-8111-111111111111"


def mock_iso_header(volume_label: str) -> bytes:
    """System area plus a primary volume descriptor carrying ``volume_label``."""
    descriptor = bytearray(2048)
    descriptor[0] = 1
    descriptor[1:6] = b"CD001"
    descriptor[6] = 1
    descriptor[40:72] = volume_label.encode("ascii").ljust(32)
    return bytes(16 * 2048) + bytes(descriptor)


class _MockHost:
    def __init__(self, id_: str, name: str, maintenance: bool = False, disconnected: bool = False) -> None:
        self.id = id_
        self.name = name
        self.maintenance_mode = maintenance
        self.connection_state = "disconnected" if disconnected else "connected"
        self.cpu_usage_percent = 23.5 if not disconnected else 0.0
        self.memory_usage_percent = 61.2 if not disconnected else 0.0


class _MockDatastore:
    def __init__(self, id_: str, name: str, type_: str, capacity_gb: float, free_gb: float,
                 dsc_id: str | None = None, accessible: bool = True) -> None:
        self.id = id_
        self.name = name
        self.type = type_
        self.capacity_gb = capacity_gb
        self.free_gb = free_gb
        self.datastore_cluster_id = dsc_id
        self.accessible = accessible


class _MockVM:
    def __init__(self, id_: str, name: str, *, installed: bool = False) -> None:
        self.id = id_
        self.name = name
        self.power_state = "poweredOff"
        self.tools_status: str | None = None
        self.tools_running_status: str | None = None
        self.tools_version_status: str | None = None
        self.guest_state: str | None = None
        self.guest_operations_ready = False
        self.guest_family: str | None = None
        self.has_guest_os = installed
        self.ip_address: str | None = None
        self.hostname: str | None = None
        self.network_id: str | None = None
        self.iso_id: str | None = None
        self.cpu = 2
        self.memory_mb = 4096
        self.disks_gb: list[int] = []
        self.powered_on_at: dt.datetime | None = None
        self.answer_media: set[str] = set()
        # Datastore paths of connected installation ISOs (Windows + Tools).
        self.installation_media: list[str] = []
        self.screenshots = 0
        self.datastore_id: str | None = None
        self.owner_job_id: str | None = None
        self.keystrokes: list[int] = []


class _MockInventory:
    """One simulated vCenter estate."""

    def __init__(self) -> None:
        self.datacenters = {
            "datacenter-21": "DC01-Corporate",
            "datacenter-22": "DC02-Lab",
        }
        self.clusters: dict[str, dict] = {
            "domain-c7": {"name": "PROD-CLUSTER", "dc": "datacenter-21", "drs": True,
                          "hosts": ["host-11", "host-12", "host-13"], "cores": 96, "memory_gb": 1536},
            "domain-c8": {"name": "EDGE-CLUSTER", "dc": "datacenter-21", "drs": False,
                          "hosts": ["host-14"], "cores": 48, "memory_gb": 512},
            "domain-c9": {"name": "LAB-CLUSTER", "dc": "datacenter-22", "drs": True,
                          "hosts": ["host-15"], "cores": 32, "memory_gb": 256},
        }
        self.hosts: dict[str, _MockHost] = {
            "host-11": _MockHost("host-11", "esx01.company.local"),
            "host-12": _MockHost("host-12", "esx02.company.local"),
            "host-13": _MockHost("host-13", "esx03.company.local", maintenance=True),
            "host-14": _MockHost("host-14", "esx04.company.local"),
            "host-15": _MockHost("host-15", "lab-esx01.company.local"),
        }
        self.resource_pools: dict[str, dict] = {
            "resgroup-31": {"name": "Resources", "cluster": "domain-c7"},
            "resgroup-32": {"name": "PROD-HIGH", "cluster": "domain-c7"},
            "resgroup-33": {"name": "PROD-STANDARD", "cluster": "domain-c7"},
            "resgroup-34": {"name": "Resources", "cluster": "domain-c8"},
            "resgroup-35": {"name": "Resources", "cluster": "domain-c9"},
        }
        self.datastores: dict[str, _MockDatastore] = {
            "datastore-41": _MockDatastore("datastore-41", "PROD-SAN-01", "VMFS", 20480, 9216, "group-p45"),
            "datastore-42": _MockDatastore("datastore-42", "PROD-SAN-02", "VMFS", 10240, 3072, "group-p45"),
            "datastore-43": _MockDatastore("datastore-43", "PROD-VSAN-01", "VSAN", 40960, 18432),
            "datastore-44": _MockDatastore("datastore-44", "LAB-SAN-01", "NFS", 5120, 2048),
            "datastore-45": _MockDatastore("datastore-45", "RETIRE-SAN", "VMFS", 2048, 256, accessible=False),
        }
        self.cluster_datastores: dict[str, set[str]] = {
            "domain-c7": {"datastore-41", "datastore-42", "datastore-43", "datastore-45"},
            "domain-c8": {"datastore-43"},
            "domain-c9": {"datastore-44"},
        }
        self.datastore_clusters = {"group-p45": ("PROD-SAN-CLUSTER", 30720.0, 12288.0)}
        self.networks: dict[str, NetworkOut] = {
            "dvportgroup-51": NetworkOut(id="dvportgroup-51", name="VLAN100-PROD", type="DISTRIBUTED_PORT_GROUP"),
            "dvportgroup-52": NetworkOut(id="dvportgroup-52", name="VLAN200-MGMT", type="DISTRIBUTED_PORT_GROUP"),
            "dvportgroup-53": NetworkOut(id="dvportgroup-53", name="VLAN300-DB", type="DISTRIBUTED_PORT_GROUP"),
            "network-54": NetworkOut(id="network-54", name="VLAN400-LAB", type="STANDARD_PORT_GROUP"),
        }
        self.network_datacenters = {
            "dvportgroup-51": "datacenter-21",
            "dvportgroup-52": "datacenter-21",
            "dvportgroup-53": "datacenter-21",
            "network-54": "datacenter-22",
        }
        self.isos: dict[str, IsoImageOut] = {
            "iso-corp-windows-2025": IsoImageOut(
                id="iso-corp-windows-2025",
                name="Windows Server 2025.iso",
                datacenter_id="datacenter-21",
                datacenter_name="DC01-Corporate",
                datastore_id="datastore-41",
                datastore_name="PROD-SAN-01",
                path="[PROD-SAN-01] ISO/Windows Server 2025.iso",
                size_bytes=5_764_607_488,
                last_modified=dt.datetime.now(dt.UTC) - dt.timedelta(days=30),
            ),
            "iso-corp-windows-2022": IsoImageOut(
                id="iso-corp-windows-2022",
                name="Windows Server 2022.iso",
                datacenter_id="datacenter-21",
                datacenter_name="DC01-Corporate",
                datastore_id="datastore-42",
                datastore_name="PROD-SAN-02",
                path="[PROD-SAN-02] ISO/Windows Server 2022.iso",
                size_bytes=5_365_624_832,
                last_modified=dt.datetime.now(dt.UTC) - dt.timedelta(days=90),
            ),
            "iso-corp-windows-11": IsoImageOut(
                id="iso-corp-windows-11",
                name="Windows 11 24H2.iso",
                datacenter_id="datacenter-21",
                datacenter_name="DC01-Corporate",
                datastore_id="datastore-41",
                datastore_name="PROD-SAN-01",
                path="[PROD-SAN-01] ISO/Windows 11 24H2.iso",
                size_bytes=5_819_484_160,
                last_modified=dt.datetime.now(dt.UTC) - dt.timedelta(days=45),
            ),
            "iso-lab-windows-2022": IsoImageOut(
                id="iso-lab-windows-2022",
                name="Windows Server 2022.iso",
                datacenter_id="datacenter-22",
                datacenter_name="DC02-Lab",
                datastore_id="datastore-44",
                datastore_name="LAB-SAN-01",
                path="[LAB-SAN-01] ISO/Windows Server 2022.iso",
                size_bytes=5_365_624_832,
                last_modified=dt.datetime.now(dt.UTC) - dt.timedelta(days=60),
            ),
            "iso-lab-ubuntu-2404": IsoImageOut(
                id="iso-lab-ubuntu-2404",
                name="Ubuntu Server 24.04 LTS.iso",
                datacenter_id="datacenter-22",
                datacenter_name="DC02-Lab",
                datastore_id="datastore-44",
                datastore_name="LAB-SAN-01",
                path="[LAB-SAN-01] ISO/ubuntu-24.04-live-server-amd64.iso",
                size_bytes=2_732_572_672,
                last_modified=dt.datetime.now(dt.UTC) - dt.timedelta(days=10),
            ),
        }
        # ISO 9660 volume labels, as Microsoft and Canonical ship them.
        self.iso_labels: dict[str, str] = {
            "[PROD-SAN-01] ISO/Windows Server 2025.iso": "SSS_X64FREE_EN-US_DV9",
            "[PROD-SAN-02] ISO/Windows Server 2022.iso": "SSS_X64FREE_EN-US_DV9",
            "[PROD-SAN-01] ISO/Windows 11 24H2.iso": "CCCOMA_X64FRE_EN-US_DV9",
            "[LAB-SAN-01] ISO/Windows Server 2022.iso": "SSS_X64FREE_EN-US_DV9",
            "[LAB-SAN-01] ISO/ubuntu-24.04-live-server-amd64.iso": "Ubuntu-Server 24.04 LTS amd64",
        }
        # Pre-existing estate demonstrating duplicate-name / IP-conflict detection.
        self.existing_vms: dict[str, _MockVM] = {}
        for index, (name, ip) in enumerate(
            [("APP-PROD-004", "10.20.30.10"), ("SQL-TEST-002", "10.20.30.11")]
        ):
            vm = _MockVM(f"vm-{70 + index}", name, installed=True)
            vm.power_state = "poweredOn"
            vm.tools_status = "toolsOk"
            vm.ip_address = ip
            vm.hostname = name.lower()
            self.existing_vms[name.upper()] = vm
        self.reserved_ips = {
            "10.20.30.1": "VLAN100 gateway",
            "10.20.30.2": "core-sw-01",
            "10.20.30.3": "core-sw-02",
            "10.20.30.10": "APP-PROD-004",
            "10.20.30.11": "SQL-TEST-002",
        }

    # helpers -------------------------------------------------------------

    def all_vms(self) -> dict[str, _MockVM]:
        return {**self.existing_vms}

    def find_vm_by_name(self, name: str) -> _MockVM | None:
        return self.all_vms().get(name.upper())


_MOCK_ESTATES: dict[str, _MockInventory] = {}


def _estate(vcenter_id: str) -> _MockInventory:
    if vcenter_id not in _MOCK_ESTATES:
        _MOCK_ESTATES[vcenter_id] = _MockInventory()
    return _MOCK_ESTATES[vcenter_id]


def reset_mock_estates() -> None:
    """Primarily for tests."""
    _MOCK_ESTATES.clear()


class MockVMwareService(VMwareService):
    name = "mock"

    # ── Discovery ────────────────────────────────────────────────────────────

    async def test_connection(self, target: VCenterTarget) -> ConnectionTestResult:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        return ConnectionTestResult(ok=True, latency_ms=12.0, detail="Mock vCenter session established.")

    async def get_datacenters(self, target: VCenterTarget) -> list[DatacenterOut]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        return [DatacenterOut(id=dc_id, name=name, vcenter_id=target.id) for dc_id, name in inv.datacenters.items()]

    async def get_clusters(self, target: VCenterTarget, datacenter_id: str) -> list[ClusterOut]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        if datacenter_id not in inv.datacenters:
            raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")
        return [
            ClusterOut(
                id=cid,
                name=info["name"],
                datacenter_id=datacenter_id,
                drs_enabled=info["drs"],
                hosts_count=len(info["hosts"]),
                total_cpu_cores=info["cores"],
                total_memory_gb=float(info["memory_gb"]),
            )
            for cid, info in inv.clusters.items()
            if info["dc"] == datacenter_id
        ]

    async def get_hosts(self, target: VCenterTarget, cluster_id: str) -> list[HostOut]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        cluster = inv.clusters.get(cluster_id)
        if cluster is None:
            raise NotFoundError(f"Cluster '{cluster_id}' does not exist.")
        hosts = []
        for host_id in cluster["hosts"]:
            host = inv.hosts[host_id]
            available = (
                host.connection_state == "connected"
                and not host.maintenance_mode
            )
            hosts.append(
                HostOut(
                    id=host.id,
                    name=host.name,
                    connection_state=host.connection_state,
                    maintenance_mode=host.maintenance_mode,
                    cpu_usage_percent=host.cpu_usage_percent,
                    memory_usage_percent=host.memory_usage_percent,
                    available_for_provisioning=available,
                )
            )
        return hosts

    async def get_resource_pools(self, target: VCenterTarget, cluster_id: str) -> list[ResourcePoolOut]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        if cluster_id not in inv.clusters:
            raise NotFoundError(f"Cluster '{cluster_id}' does not exist.")
        return [
            ResourcePoolOut(id=pid, name=pool["name"], cluster_id=cluster_id)
            for pid, pool in inv.resource_pools.items()
            if pool["cluster"] == cluster_id
        ]

    async def get_datastores(self, target: VCenterTarget, cluster_id: str) -> list[DatastoreOut]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        if cluster_id not in inv.clusters:
            raise NotFoundError(f"Cluster '{cluster_id}' does not exist.")
        results = []
        allowed = inv.cluster_datastores.get(cluster_id, set())
        for ds in inv.datastores.values():
            if ds.id not in allowed:
                continue
            usage = round((ds.capacity_gb - ds.free_gb) / ds.capacity_gb * 100, 1) if ds.capacity_gb else 100.0
            results.append(
                DatastoreOut(
                    id=ds.id,
                    name=ds.name,
                    type=ds.type,
                    capacity_gb=ds.capacity_gb,
                    free_gb=round(ds.free_gb, 1),
                    usage_percent=usage,
                    accessible=ds.accessible,
                    datastore_cluster_id=ds.datastore_cluster_id,
                )
            )
        return results

    async def get_datastore_clusters(self, target: VCenterTarget, cluster_id: str) -> list[DatastoreClusterOut]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        if cluster_id not in inv.clusters:
            raise NotFoundError(f"Cluster '{cluster_id}' does not exist.")
        cluster_datastores = inv.cluster_datastores.get(cluster_id, set())
        visible_clusters = {
            inv.datastores[datastore_id].datastore_cluster_id
            for datastore_id in cluster_datastores
            if datastore_id in inv.datastores
        }
        return [
            DatastoreClusterOut(id=dsc_id, name=name, capacity_gb=cap, free_gb=free)
            for dsc_id, (name, cap, free) in inv.datastore_clusters.items()
            if dsc_id in visible_clusters
        ]

    async def get_networks(self, target: VCenterTarget, datacenter_id: str) -> list[NetworkOut]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        if datacenter_id not in inv.datacenters:
            raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")
        networks = [
            network
            for network_id, network in inv.networks.items()
            if inv.network_datacenters.get(network_id) == datacenter_id
        ]
        return sorted(networks, key=lambda n: n.name)

    async def get_isos(self, target: VCenterTarget, datacenter_id: str) -> list[IsoImageOut]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        if datacenter_id not in inv.datacenters:
            raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")
        return sorted(
            [image for image in inv.isos.values() if image.datacenter_id == datacenter_id],
            key=lambda image: image.name,
        )

    async def read_datastore_file(
        self,
        target: VCenterTarget,
        datacenter_id: str,
        datastore_path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        if datacenter_id not in inv.datacenters:
            raise NotFoundError(f"Datacenter '{datacenter_id}' does not exist.")
        label = inv.iso_labels.get(datastore_path)
        if label is not None:
            return mock_iso_header(label)[:max_bytes]
        if datastore_path.lower().endswith(".png"):
            return b"\x89PNG\r\n\x1a\n"[:max_bytes]
        raise InfraOperationError(
            f"'{datastore_path}' was not found on its datastore.",
            reason="The file was moved or deleted.",
            recommended_action="Refresh the inventory and select the file again.",
            retryable=False,
        )

    # ── Inventory queries ────────────────────────────────────────────────────

    async def vm_exists(self, target: VCenterTarget, vm_name: str) -> bool:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        return _estate(target.id).find_vm_by_name(vm_name) is not None

    async def get_vm_info(self, target: VCenterTarget, vm_name: str) -> PowerStateInfo | None:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        vm = _estate(target.id).find_vm_by_name(vm_name)
        if vm is None:
            return None
        ips = [vm.ip_address] if vm.ip_address else []
        return PowerStateInfo(
            power_state=vm.power_state,
            tools_status=vm.tools_status,
            tools_running_status=vm.tools_running_status,
            tools_version_status=vm.tools_version_status,
            guest_state=vm.guest_state,
            guest_operations_ready=vm.guest_operations_ready,
            guest_family=vm.guest_family,
            ip_addresses=ips,
            guest_host_name=vm.hostname or (vm.name.upper() if vm.guest_operations_ready else None),
        )

    async def get_used_ips(self, target: VCenterTarget) -> dict[str, str]:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        inv = _estate(target.id)
        used = dict(inv.reserved_ips)
        for vm in inv.existing_vms.values():
            if vm.ip_address:
                used[vm.ip_address] = vm.name
        return used

    async def resolve_vm_id(self, target: VCenterTarget, vm_name: str) -> str | None:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        vm = _estate(target.id).find_vm_by_name(vm_name)
        return vm.id if vm is not None else None

    async def find_vm_ownership(self, target: VCenterTarget, vm_name: str) -> VmOwnership | None:
        await asyncio.sleep(_LATENCY_DISCOVERY)
        vm = _estate(target.id).find_vm_by_name(vm_name)
        if vm is None:
            return None
        return VmOwnership(vm_id=vm.id, name=vm.name, owner_job_id=vm.owner_job_id)

    async def tag_vm_owner(self, target: VCenterTarget, vm_id: str, job_id: str) -> None:
        vm = self._require_vm(target, vm_id)
        if vm.owner_job_id is not None and vm.owner_job_id != job_id:
            raise InfraOperationError(
                f"VM '{vm.name}' belongs to another InfraOps job.",
                reason="Ownership marker mismatch.",
                recommended_action="Choose a different VM name.",
                retryable=False,
            )
        vm.owner_job_id = job_id

    # ── Lifecycle operations ─────────────────────────────────────────────────

    async def create_vm(self, target: VCenterTarget, spec: VmCreateSpec) -> VmRef:
        log.info("MOCK create VM: name=%s cluster=%s", spec.vm_name, spec.cluster_id)
        inv = _estate(target.id)
        cluster = inv.clusters.get(spec.cluster_id)
        if cluster is None or cluster["dc"] != spec.datacenter_id:
            raise InfraOperationError(
                f"Cluster '{spec.cluster_id}' is not available in the selected datacenter.",
                reason="The placement inventory changed after validation.",
                recommended_action="Re-open the wizard and select the infrastructure again.",
                retryable=False,
            )
        if spec.host_id and spec.host_id not in cluster["hosts"]:
            raise InfraOperationError(
                f"Host '{spec.host_id}' does not belong to cluster '{spec.cluster_id}'.",
                reason="The requested host and cluster do not match.",
                recommended_action="Select a host from the chosen cluster.",
                retryable=False,
            )
        if spec.resource_pool_id:
            pool = inv.resource_pools.get(spec.resource_pool_id)
            if pool is None or pool["cluster"] != spec.cluster_id:
                raise InfraOperationError(
                    f"Resource pool '{spec.resource_pool_id}' was not found in the cluster.",
                    reason="Resource pool removed or moved after validation.",
                    recommended_action="Re-select the placement target and retry.",
                    retryable=False,
                )
        if inv.find_vm_by_name(spec.vm_name) is not None:
            raise InfraOperationError(
                f"A virtual machine named '{spec.vm_name}' already exists.",
                reason="Duplicate VM name in the vCenter inventory.",
                recommended_action="Choose a different VM name and resubmit the request.",
                retryable=False,
            )
        required_gb = spec.os_disk.size_gb
        allowed_datastores = inv.cluster_datastores.get(spec.cluster_id, set())
        candidates = [
            datastore for datastore in inv.datastores.values()
            if datastore.id in allowed_datastores
            and datastore.accessible
            and datastore.free_gb >= required_gb
        ]
        if spec.datastore_id:
            candidates = [datastore for datastore in candidates if datastore.id == spec.datastore_id]
        if not candidates:
            raise InfraOperationError(
                "No selected datastore has enough accessible capacity for the VM.",
                reason=f"The OS disk requires approximately {required_gb} GB.",
                recommended_action="Select another datastore or reduce the requested disk capacity.",
                retryable=False,
            )
        datastore = max(candidates, key=lambda entry: entry.free_gb)

        selected_iso = inv.isos.get(spec.iso_id)
        iso_datastore = inv.datastores.get(selected_iso.datastore_id) if selected_iso is not None else None
        if (
            selected_iso is None
            or selected_iso.datacenter_id != spec.datacenter_id
            or selected_iso.datastore_id not in allowed_datastores
            or iso_datastore is None
            or not iso_datastore.accessible
        ):
            raise InfraOperationError(
                "The selected ISO is not available to the target compute cluster.",
                reason="The ISO belongs to another datacenter or an inaccessible datastore.",
                recommended_action="Refresh the ISO inventory and select another image.",
                retryable=False,
            )

        datastore.free_gb -= required_gb
        await asyncio.sleep(_LATENCY_CREATE)
        vm_id = f"vm-{uuid.uuid4().hex[:8]}"
        vm = _MockVM(vm_id, spec.vm_name)
        vm.cpu = spec.cpu
        vm.memory_mb = spec.memory_mb
        vm.disks_gb = [required_gb]
        vm.datastore_id = datastore.id
        vm.installation_media = [selected_iso.path, "[] /vmimages/tools-isoimages/windows.iso"]
        vm.owner_job_id = spec.job_id
        inv.existing_vms[spec.vm_name.upper()] = vm
        return VmRef(id=vm_id, name=spec.vm_name)

    async def configure_hardware(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        cpu: int,
        memory_mb: int,
    ) -> None:
        await asyncio.sleep(_LATENCY_RECONFIG)
        vm = self._require_vm(target, vm_id)
        vm.cpu = cpu
        vm.memory_mb = memory_mb
        log.info("MOCK hardware configured: %s cpu=%s mem=%sMB", vm.name, cpu, memory_mb)

    async def attach_network(
        self,
        target: VCenterTarget,
        vm_id: str,
        network_id: str,
        adapter_type: AdapterType,
        datacenter_id: str,
    ) -> None:
        await asyncio.sleep(_LATENCY_RECONFIG)
        inv = _estate(target.id)
        vm = self._require_vm(target, vm_id)
        if network_id not in inv.networks:
            raise InfraOperationError(
                f"Network '{network_id}' no longer exists on {target.host}.",
                reason="Port group removed or renamed.",
                recommended_action="Select a valid port group and retry the network stage.",
                retryable=True,
            )
        if datacenter_id not in inv.datacenters:
            raise InfraOperationError(
                "The selected datacenter no longer exists.",
                reason="The placement inventory changed after validation.",
                recommended_action="Refresh the infrastructure inventory and retry.",
                retryable=False,
            )
        if inv.network_datacenters.get(network_id) != datacenter_id:
            raise InfraOperationError(
                "The selected network belongs to another datacenter.",
                reason="Network scope changed or the request contains a stale selection.",
                recommended_action="Select a network from the target datacenter.",
                retryable=False,
            )
        vm.network_id = network_id
        log.info("MOCK network attached: %s -> %s (%s)", vm.name, inv.networks[network_id].name, adapter_type.value)

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
        await asyncio.sleep(_LATENCY_RECONFIG)
        vm = self._require_vm(target, vm_id)
        if not content:
            raise InfraOperationError(
                "The generated answer media was empty.",
                reason="No answer-file content was supplied.",
                recommended_action="Retry the unattended installation preparation stage.",
                retryable=True,
            )
        path = f"[mock-datastore] infraops-unattend/{file_name}"
        vm.answer_media.add(path)
        return TemporaryMediaRef(datastore_path=path)

    async def remove_answer_media(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        datacenter_id: str,
        datastore_path: str,
    ) -> None:
        vm = self._require_vm(target, vm_id)
        vm.answer_media.discard(datastore_path)

    async def power_on(self, target: VCenterTarget, vm_id: str) -> None:
        await asyncio.sleep(_LATENCY_POWER_ON)
        vm = self._require_vm(target, vm_id)
        if vm.power_state == "poweredOn":
            raise InfraOperationError(
                f"VM '{vm.name}' is already powered on.",
                reason="The VM was powered on outside this stage.",
                recommended_action="Reset the VM or retry the stage.",
                retryable=True,
            )
        vm.power_state = "poweredOn"
        vm.powered_on_at = dt.datetime.now(dt.UTC)
        if vm.has_guest_os:
            vm.guest_state = "running"
        log.info("MOCK powered on: %s", vm.name)

    async def reset(self, target: VCenterTarget, vm_id: str) -> None:
        vm = self._require_vm(target, vm_id)
        vm.powered_on_at = dt.datetime.now(dt.UTC)

    async def send_keystrokes(self, target: VCenterTarget, vm_id: str, usb_hid_usages: list[int]) -> int:
        vm = self._require_vm(target, vm_id)
        vm.keystrokes.extend(usb_hid_usages)
        return len(usb_hid_usages)

    async def wait_for_tools(self, target: VCenterTarget, vm_id: str, timeout_seconds: float) -> None:
        vm = self._require_vm(target, vm_id)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            # Windows Setup only runs unattended when the answer media is attached.
            installing = vm.answer_media and vm.installation_media
            if vm.powered_on_at is not None and (vm.has_guest_os or installing):
                elapsed = (dt.datetime.now(dt.UTC) - vm.powered_on_at).total_seconds()
                if elapsed >= _TOOLS_READY_DELAY_SECONDS:
                    vm.has_guest_os = True
                    vm.guest_state = "running"
                    vm.guest_family = "windowsGuest"
                    vm.tools_status = "toolsOk"
                    vm.tools_running_status = "guestToolsRunning"
                    vm.tools_version_status = "guestToolsCurrent"
                    vm.guest_operations_ready = True
                    log.info("MOCK VMware Tools ready: %s", vm.name)
                    return
            if asyncio.get_running_loop().time() >= deadline:
                raise InfraOperationError(
                    f"VMware Tools did not become ready on '{vm.name}' within the configured timeout.",
                    reason=f"Tools heartbeat absent after {int(timeout_seconds)} seconds.",
                    recommended_action=(
                        "Open the console screenshot attached to this step to see where the guest "
                        "stopped, then retry the failed stage."
                    ),
                    technical_detail=(
                        f"mock: tools_ready_delay={_TOOLS_READY_DELAY_SECONDS}s "
                        f"timeout={timeout_seconds}s"
                    ),
                    retryable=True,
                )
            await asyncio.sleep(0.25)

    async def detach_installation_media(self, target: VCenterTarget, vm_id: str) -> list[str]:
        await asyncio.sleep(_LATENCY_RECONFIG)
        vm = self._require_vm(target, vm_id)
        detached, vm.installation_media = vm.installation_media, []
        return detached

    async def add_data_disks(self, target: VCenterTarget, vm_id: str, disks: list[DiskSpec]) -> int:
        await asyncio.sleep(_LATENCY_RECONFIG)
        vm = self._require_vm(target, vm_id)
        missing = disks[max(len(vm.disks_gb) - 1, 0):]
        datastore = _estate(target.id).datastores.get(vm.datastore_id or "")
        required_gb = sum(disk.size_gb for disk in missing)
        if datastore is not None:
            if datastore.free_gb < required_gb:
                raise InfraOperationError(
                    f"Datastore '{datastore.name}' has only {datastore.free_gb:.0f} GB free "
                    f"but the data disks need {required_gb} GB.",
                    reason="Insufficient datastore capacity.",
                    recommended_action="Free space on the datastore, then retry this stage.",
                    retryable=True,
                )
            datastore.free_gb -= required_gb
        vm.disks_gb.extend(disk.size_gb for disk in missing)
        return len(missing)

    async def check_host_https(self, target: VCenterTarget, host_names: list[str]) -> dict[str, str | None]:
        return {name: None for name in host_names}

    async def capture_screenshot(self, target: VCenterTarget, vm_id: str) -> str:
        vm = self._require_vm(target, vm_id)
        vm.screenshots += 1
        return f"[mock-datastore] {vm.name}/{vm.name}-{vm.screenshots}.png"

    # ── internal ─────────────────────────────────────────────────────────────

    def _require_vm(self, target: VCenterTarget, vm_id: str):
        inv = _estate(target.id)
        for vm in inv.existing_vms.values():
            if vm.id == vm_id:
                return vm
        raise ServiceUnavailableError(f"Mock VM '{vm_id}' is not registered in the simulated estate.")


def get_mock_inventory(vcenter_id: str) -> _MockInventory:
    """Expose the simulated estate to other mock providers (guest ops, IPAM…)."""
    return _estate(vcenter_id)
