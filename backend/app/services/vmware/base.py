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


# Every VM InfraOps creates carries the id of the job that created it, both in
# ``extraConfig`` and (atomically at creation time) as an annotation line. A
# job only ever resumes on a VM carrying its own marker; any other VM with the
# requested name is treated as "name taken" and is never modified.
OWNER_EXTRA_CONFIG_KEY = "infraops.job_id"
OWNER_ANNOTATION_PREFIX = "infraops-job-id:"


def owner_annotation(description: str, job_id: str | None) -> str:
    """Return the VM annotation carrying the ownership marker."""
    if not job_id:
        return description
    marker = f"{OWNER_ANNOTATION_PREFIX} {job_id}"
    return f"{description}\n\n{marker}" if description else marker


def owner_from_annotation(annotation: str | None) -> str | None:
    for line in (annotation or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(OWNER_ANNOTATION_PREFIX):
            value = stripped[len(OWNER_ANNOTATION_PREFIX):].strip()
            return value or None
    return None


def vcenter_ssl_context(target: VCenterTarget):
    """TLS context for every connection to vCenter (SOAP, REST, file transfers).

    Verifies against ``VCENTER_CA_FILE`` when set, otherwise the system trust
    store. Returns ``False`` for connections whose administrator disabled
    certificate verification.
    """
    import os
    import ssl

    from app.core.config import get_settings
    from app.core.errors import InfraOperationError

    ensure_tls_policy(target)
    if not target.verify_ssl:
        return False
    ca_file = get_settings().vcenter_ca_file or None
    if ca_file and not os.path.isfile(ca_file):
        raise InfraOperationError(
            "The configured vCenter CA bundle was not found.",
            reason=f"VCENTER_CA_FILE points to '{ca_file}', which does not exist inside the container.",
            recommended_action=(
                "Place the PEM bundle in the mounted certs directory (./certs on the host) and "
                "restart the backend and worker, or clear VCENTER_CA_FILE to use the system trust store."
            ),
            retryable=False,
        )
    try:
        return ssl.create_default_context(cafile=ca_file)
    except (ssl.SSLError, OSError) as exc:
        raise InfraOperationError(
            "The configured vCenter CA bundle could not be loaded.",
            reason=f"VCENTER_CA_FILE '{ca_file}' is not a readable PEM certificate bundle.",
            recommended_action="Provide the CA certificate(s) in PEM format and restart the backend and worker.",
            technical_detail=f"{type(exc).__name__}: {exc}",
            retryable=False,
        ) from exc


def ensure_tls_policy(target: VCenterTarget) -> None:
    """Log every connection whose certificate verification an administrator
    turned off on the vCenter connection (traffic is still encrypted)."""
    from app.core.logging import get_logger

    if target.verify_ssl:
        return
    get_logger(__name__).warning(
        "Connecting to vCenter '%s' (%s) without TLS certificate verification.", target.name, target.host
    )


def vcenter_ssl_context_or_unverified(target: VCenterTarget):
    """pyVmomi needs an SSLContext object even when verification is disabled."""
    import ssl

    context = vcenter_ssl_context(target)
    if context is False:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


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
    job_id: str | None = None


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
    job_id: str | None = None


@dataclass(frozen=True)
class VmRef:
    id: str
    name: str


@dataclass(frozen=True)
class VmOwnership:
    """A VM found by name together with the InfraOps job marker it carries."""

    vm_id: str
    name: str
    owner_job_id: str | None


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
    # Computer name as reported by VMware Tools (guest.hostName).
    guest_host_name: str | None = None
    # Guest OS the VM is configured for (config.guestId, e.g. from the OVF),
    # known before the guest has ever booted.
    configured_guest_id: str | None = None

    @property
    def configured_for_windows(self) -> bool:
        return (self.configured_guest_id or "").casefold().startswith("win")


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

    @abstractmethod
    async def find_vm_ownership(self, target: VCenterTarget, vm_name: str) -> VmOwnership | None:
        """Return the VM with this name and its InfraOps job marker, or None."""

    @abstractmethod
    async def tag_vm_owner(self, target: VCenterTarget, vm_id: str, job_id: str) -> None:
        """Write the ownership marker into the VM's ``extraConfig``."""

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
        firmware: FirmwareType | None,
        secure_boot: bool,
    ) -> None:
        """``firmware=None`` keeps the VM's firmware and Secure Boot setting."""

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
