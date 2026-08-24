"""VM provisioning request/response contracts.

These schemas are the single source of truth for request validation — the
frontend mirrors them with Zod, but the backend never trusts the frontend.
All identifiers referencing platform records (vCenters, sites, packages,
applications) are UUIDs; VMware objects are addressed by stable internal
managed-object references (strings).
"""

from __future__ import annotations

import enum
import ipaddress
import re

from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator

# ── Shared enumerations ──────────────────────────────────────────────────────


class DiskProvisioning(str, enum.Enum):
    THIN = "thin"
    THICK = "thick"


class FirmwareType(str, enum.Enum):
    BIOS = "BIOS"
    EFI = "EFI"


class IpMode(str, enum.Enum):
    DHCP = "DHCP"
    STATIC = "STATIC"


class AdapterType(str, enum.Enum):
    VMXNET3 = "VMXNET3"
    E1000E = "E1000E"


VM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9\-.]{0,61}[A-Za-z0-9])?$")
DNS_DOMAIN_PATTERN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$", re.IGNORECASE)
GUID_PATTERN = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)
THUMBPRINT_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _validate_ipv4(value: str, field: str) -> str:
    try:
        parsed = ipaddress.IPv4Address(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field}: '{value}' is not a valid IPv4 address.") from exc
    return str(parsed)


# ── Request fragments ────────────────────────────────────────────────────────


class VmSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=64, pattern=VM_NAME_PATTERN.pattern)
    description: str = Field(default="", max_length=500)


class ComputeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vcenter_id: UUID4
    site_id: UUID4
    datacenter_id: str = Field(min_length=1, max_length=120)
    cluster_id: str = Field(min_length=1, max_length=120)
    host_id: str | None = Field(default=None, max_length=120)
    resource_pool_id: str | None = Field(default=None, max_length=120)


class DiskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_gb: int = Field(ge=1, le=8000)
    provisioning: DiskProvisioning = DiskProvisioning.THIN
    datastore_id: str | None = Field(default=None, max_length=120)


class HardwareSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu: int = Field(ge=1, le=256)
    memory_mb: int = Field(ge=512, le=8_388_608)
    firmware: FirmwareType = FirmwareType.EFI
    secure_boot: bool = False
    disks: list[DiskSpec] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _secure_boot_requires_efi(self) -> "HardwareSpec":
        if self.secure_boot and self.firmware != FirmwareType.EFI:
            raise ValueError("secure_boot requires UEFI (EFI) firmware.")
        return self


class DomainJoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=3, max_length=255, pattern=DNS_DOMAIN_PATTERN.pattern)
    ou: str | None = Field(default=None, max_length=400)
    credential_secret_ref: str = Field(min_length=2, max_length=150)


class GuestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=120)
    hostname: str | None = Field(default=None, max_length=64, pattern=VM_NAME_PATTERN.pattern)
    timezone: str | None = Field(default=None, max_length=100)
    domain_join: DomainJoinSpec | None = None

    @property
    def effective_hostname(self) -> str:
        return self.hostname or ""


class Ipv4Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    prefix: int = Field(ge=8, le=32)
    gateway: str
    dns_servers: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_addresses(self) -> "Ipv4Config":
        object.__setattr__(self, "address", _validate_ipv4(self.address, "IP address"))
        object.__setattr__(self, "gateway", _validate_ipv4(self.gateway, "Default gateway"))

        network = ipaddress.ip_network(f"{self.address}/{self.prefix}", strict=False)
        addr = ipaddress.IPv4Address(self.address)
        gw = ipaddress.IPv4Address(self.gateway)

        if addr == network.network_address:
            raise ValueError("IP address equals the network address and cannot be assigned to a host.")
        if addr == network.broadcast_address:
            raise ValueError("IP address equals the broadcast address and cannot be assigned to a host.")
        if gw not in network:
            raise ValueError(
                f"Default gateway {self.gateway} is outside the {network.with_prefixlen} subnet."
            )
        if gw == addr:
            raise ValueError("Default gateway must differ from the VM's own IP address.")

        seen: set[str] = set()
        normalized_dns: list[str] = []
        for dns in self.dns_servers:
            normalized = _validate_ipv4(dns, "DNS server")
            if normalized in seen:
                raise ValueError(f"Duplicate DNS server: {normalized}")
            seen.add(normalized)
            normalized_dns.append(normalized)
        object.__setattr__(self, "dns_servers", normalized_dns)
        return self

    @property
    def subnet_mask(self) -> str:
        return str(ipaddress.ip_network(f"0.0.0.0/{self.prefix}").netmask)


class NetworkSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network_id: str = Field(min_length=1, max_length=120)
    adapter_type: AdapterType = AdapterType.VMXNET3
    mode: IpMode
    ipv4: Ipv4Config | None = None

    @model_validator(mode="after")
    def _static_requires_ipv4(self) -> "NetworkSpec":
        if self.mode == IpMode.STATIC and self.ipv4 is None:
            raise ValueError("ipv4 configuration is required when mode is STATIC.")
        return self


class ProvisioningRequest(BaseModel):
    """The complete, typed provisioning request (spec §36)."""

    model_config = ConfigDict(extra="forbid")

    vm: VmSpec
    compute: ComputeSpec
    hardware: HardwareSpec
    guest: GuestSpec
    network: NetworkSpec
    certificate_package_ids: list[UUID4] = Field(default_factory=list, max_length=20)
    application_ids: list[UUID4] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _hostname_defaults_to_vm_name(self) -> "ProvisioningRequest":
        if not self.guest.hostname:
            self.guest.hostname = self.vm.name
        return self

    @property
    def total_disk_gb(self) -> int:
        return sum(disk.size_gb for disk in self.hardware.disks)


# ── Dry-run / preflight ──────────────────────────────────────────────────────


class CheckStatus(str, enum.Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class PreflightCheck(BaseModel):
    code: str
    label: str
    status: CheckStatus
    detail: str = ""
    blocking: bool = True


class PreflightReport(BaseModel):
    ready: bool
    checks: list[PreflightCheck]
    summary: str


# ── IP conflict validation ───────────────────────────────────────────────────


class ConflictProviderStatus(str, enum.Enum):
    NO_CONFLICT = "NO_CONFLICT"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ERROR = "ERROR"


class ProviderResult(BaseModel):
    provider: str
    status: ConflictProviderStatus
    detail: str = ""


class IpConflictCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    prefix: int = Field(ge=8, le=32)


class IpConflictReport(BaseModel):
    address: str
    prefix: int
    providers: list[ProviderResult]
    conflict_detected: bool
    confidence_note: str
