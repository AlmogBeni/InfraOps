"""VMware service interface and shared value objects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

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
    TemplateOut,
)
from app.schemas.provisioning import AdapterType, DiskSpec, FirmwareType


@dataclass(frozen=True)
class VCenterTarget:
    """Connection descriptor resolved from the database by callers.

    Raw credentials are never carried here — only secret references which the
    implementation resolves from encrypted backend storage at connect time.
    """

    id: str
    name: str
    host: str
    port: int
    username_secret_ref: str
    password_secret_ref: str
    verify_ssl: bool


@dataclass(frozen=True)
class CloneSpec:
    template_id: str
    vm_name: str
    datacenter_id: str
    description: str = ""
    cluster_id: str = ""
    host_id: str | None = None
    resource_pool_id: str | None = None
    datastore_id: str | None = None
    cpu: int = 2
    memory_mb: int = 4096
    disks: tuple[DiskSpec, ...] = ()
    network_id: str = ""
    adapter_type: AdapterType = AdapterType.VMXNET3
    firmware: FirmwareType = FirmwareType.EFI
    secure_boot: bool = False


@dataclass(frozen=True)
class BlankVmSpec:
    vm_name: str
    datacenter_id: str
    description: str = ""
    cluster_id: str = ""
    host_id: str | None = None
    resource_pool_id: str | None = None
    datastore_id: str | None = None
    cpu: int = 2
    memory_mb: int = 4096
    disks: tuple[DiskSpec, ...] = ()
    firmware: FirmwareType = FirmwareType.EFI
    secure_boot: bool = False
    iso_id: str | None = None


@dataclass(frozen=True)
class VmRef:
    id: str
    name: str


@dataclass(frozen=True)
class TemporaryMediaRef:
    datastore_path: str


@dataclass
class PowerStateInfo:
    power_state: str  # poweredOn | poweredOff | suspended | unknown
    tools_status: str | None = None  # toolsOk | toolsOld | toolsNotRunning | None
    tools_running_status: str | None = None
    tools_version_status: str | None = None
    guest_state: str | None = None
    guest_operations_ready: bool = False
    guest_family: str | None = None
    ip_addresses: list[str] = field(default_factory=list)
    host_id: str | None = None


class VMwareService(ABC):
    """Abstract vSphere operations used by provisioning pipelines and discovery APIs."""

    name: str = "abstract"

    # ── Discovery ────────────────────────────────────────────────────────────

    @abstractmethod
    async def test_connection(self, target: VCenterTarget) -> ConnectionTestResult: ...

    @abstractmethod
    async def get_datacenters(self, target: VCenterTarget) -> list[DatacenterOut]: ...

    @abstractmethod
    async def get_clusters(self, target: VCenterTarget, datacenter_id: str) -> list[ClusterOut]: ...

    @abstractmethod
    async def get_hosts(self, target: VCenterTarget, cluster_id: str) -> list[HostOut]: ...

    @abstractmethod
    async def get_resource_pools(self, target: VCenterTarget, cluster_id: str) -> list[ResourcePoolOut]: ...

    @abstractmethod
    async def get_datastores(self, target: VCenterTarget, cluster_id: str) -> list[DatastoreOut]: ...

    @abstractmethod
    async def get_datastore_clusters(self, target: VCenterTarget, cluster_id: str) -> list[DatastoreClusterOut]: ...

    @abstractmethod
    async def get_networks(self, target: VCenterTarget, datacenter_id: str) -> list[NetworkOut]: ...

    @abstractmethod
    async def get_templates(self, target: VCenterTarget, datacenter_id: str | None = None) -> list[TemplateOut]: ...

    @abstractmethod
    async def get_isos(self, target: VCenterTarget, datacenter_id: str) -> list[IsoImageOut]: ...

    # ── Inventory queries ────────────────────────────────────────────────────

    @abstractmethod
    async def vm_exists(self, target: VCenterTarget, vm_name: str) -> bool: ...

    @abstractmethod
    async def get_vm_info(self, target: VCenterTarget, vm_name: str) -> PowerStateInfo | None: ...

    @abstractmethod
    async def get_used_ips(self, target: VCenterTarget) -> dict[str, str]:
        """Map of IP address -> VM name currently registered in guest inventories."""

    @abstractmethod
    async def resolve_vm_id(self, target: VCenterTarget, vm_name: str) -> str | None:
        """Return the managed-object id for a VM name, or None when absent."""

    # ── Lifecycle operations ─────────────────────────────────────────────────

    @abstractmethod
    async def clone_from_template(self, target: VCenterTarget, spec: CloneSpec) -> VmRef: ...

    @abstractmethod
    async def create_blank_vm(self, target: VCenterTarget, spec: BlankVmSpec) -> VmRef: ...

    @abstractmethod
    async def configure_hardware(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        cpu: int,
        memory_mb: int,
        disks: list[DiskSpec],
        firmware: FirmwareType,
        secure_boot: bool,
    ) -> None: ...

    @abstractmethod
    async def attach_network(
        self,
        target: VCenterTarget,
        vm_id: str,
        network_id: str,
        adapter_type: AdapterType,
        datacenter_id: str,
    ) -> None: ...

    @abstractmethod
    async def attach_temporary_floppy(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        datacenter_id: str,
        datastore_id: str | None,
        file_name: str,
        content: bytes,
    ) -> TemporaryMediaRef:
        """Upload and attach ephemeral media. Callers must never persist ``content``."""

    @abstractmethod
    async def remove_temporary_floppy(
        self,
        target: VCenterTarget,
        vm_id: str,
        *,
        datacenter_id: str,
        datastore_path: str,
    ) -> None: ...

    @abstractmethod
    async def mount_tools_installer(self, target: VCenterTarget, vm_id: str) -> bool:
        """Best-effort request to mount the vSphere-provided VMware Tools image."""

    @abstractmethod
    async def power_on(self, target: VCenterTarget, vm_id: str) -> None: ...

    @abstractmethod
    async def wait_for_tools(
        self,
        target: VCenterTarget,
        vm_id: str,
        timeout_seconds: float,
        *,
        mount_if_missing: bool = False,
    ) -> None:
        """Block until Tools reports ready.

        ``mount_if_missing`` only supplies the Tools installation media. It is
        valid for InfraOps' Windows unattended bootstrap, whose installer runs
        inside Windows at first logon; it must not be treated as installation.
        """
