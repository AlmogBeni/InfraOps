"""VM provisioning request/response contracts.

These schemas are the single source of truth for request validation — the
frontend mirrors them with Zod, but the backend never trusts the frontend.
All identifiers referencing platform records (vCenters, packages,
applications) are UUIDs; VMware objects are addressed by stable internal
managed-object references (strings).
"""

from __future__ import annotations

import enum
import ipaddress
import re
from typing import Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.secret_references import SECRET_REFERENCE_PATTERN

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


class VmSourceType(enum.StrEnum):
    BLANK = "blank"
    TEMPLATE = "template"


class IdentityPolicyVersion(enum.StrEnum):
    """Controls how a request derives the Windows/AD computer identity."""

    V1 = "v1"
    V2 = "v2"


VM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9\-.]{0,61}[A-Za-z0-9])?$")
WINDOWS_COMPUTER_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
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

    @model_validator(mode="before")
    @classmethod
    def _discard_legacy_site_id(cls, value: object) -> object:
        """Accept drafts created before site-based placement was removed."""
        if isinstance(value, dict) and "site_id" in value:
            value = dict(value)
            value.pop("site_id", None)
        return value

    vcenter_id: UUID4
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
    def _secure_boot_requires_efi(self) -> HardwareSpec:
        if self.secure_boot and self.firmware != FirmwareType.EFI:
            raise ValueError("secure_boot requires UEFI (EFI) firmware.")
        return self


class DomainJoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=3, max_length=255, pattern=DNS_DOMAIN_PATTERN.pattern)
    ou: str | None = Field(default=None, max_length=400)
    # v1 stored jobs can contain pre-version reference names. The enclosing
    # request applies the strict provider-path grammar conditionally for v2.
    credential_secret_ref: str = Field(min_length=2, max_length=150)

    @field_validator("domain", mode="before")
    @classmethod
    def _normalize_domain(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().rstrip(".").lower()
        return value


class GuestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str | None = Field(default=None, min_length=1, max_length=120)
    iso_id: str | None = Field(default=None, min_length=1, max_length=2048)
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
    def _validate_addresses(self) -> Ipv4Config:
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
    def _static_requires_ipv4(self) -> NetworkSpec:
        if self.mode == IpMode.STATIC and self.ipv4 is None:
            raise ValueError("ipv4 configuration is required when mode is STATIC.")
        return self


class ProvisioningRequest(BaseModel):
    """The complete, typed provisioning request (spec §36)."""

    model_config = ConfigDict(extra="forbid")

    # Defaults to template for compatibility with requests created before
    # source selection became an explicit part of the contract.
    source_type: VmSourceType = VmSourceType.TEMPLATE
    # New submissions use the VM name as the single Windows/AD identity.
    # Stored payload readers explicitly mark pre-policy jobs as v1 so their
    # historical guest.hostname value remains authoritative.
    identity_policy_version: IdentityPolicyVersion = IdentityPolicyVersion.V2
    vm: VmSpec
    compute: ComputeSpec
    hardware: HardwareSpec
    guest: GuestSpec
    network: NetworkSpec
    certificate_package_ids: list[UUID4] = Field(default_factory=list, max_length=20)
    application_ids: list[UUID4] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _validate_source(self) -> ProvisioningRequest:
        if self.source_type == VmSourceType.TEMPLATE and not self.guest.template_id:
            raise ValueError("guest.template_id is required when source_type is 'template'.")
        if self.source_type == VmSourceType.TEMPLATE and self.guest.iso_id is not None:
            raise ValueError("guest.iso_id must be null when source_type is 'template'.")
        if self.source_type == VmSourceType.BLANK:
            if self.guest.template_id is not None:
                raise ValueError("guest.template_id must be null when source_type is 'blank'.")
            if self.guest.hostname or self.guest.timezone or self.guest.domain_join:
                raise ValueError(
                    "Guest customization is unavailable for a blank VM until an operating system is installed."
                )
            if self.certificate_package_ids or self.application_ids:
                raise ValueError(
                    "Certificates and applications cannot be installed on a blank VM without an operating system."
                )
        else:
            short_name = (
                (
                    self.guest.hostname or self.vm.name
                    if self.identity_policy_version == IdentityPolicyVersion.V1
                    else self.vm.name
                )
                if self.guest.domain_join is not None
                else (self.guest.hostname or self.vm.name)
            )
            if self.identity_policy_version == IdentityPolicyVersion.V2 and (
                not WINDOWS_COMPUTER_NAME_PATTERN.fullmatch(short_name)
                or short_name.isdigit()
            ):
                field = (
                    "guest.hostname"
                    if (
                        self.guest.domain_join is None
                        or self.identity_policy_version == IdentityPolicyVersion.V1
                    )
                    else "vm.name"
                )
                raise ValueError(
                    f"{field} must be a valid Windows computer name "
                    "(letters, numbers and hyphens only; not all-numeric; maximum 63 characters)."
                )
            if self.guest.domain_join is not None:
                if (
                    self.identity_policy_version == IdentityPolicyVersion.V2
                    and not SECRET_REFERENCE_PATTERN.fullmatch(
                        self.guest.domain_join.credential_secret_ref
                    )
                ):
                    raise ValueError(
                        "guest.domain_join.credential_secret_ref must be a safe lowercase, "
                        "slash-separated secret reference."
                    )
                if (
                    self.identity_policy_version == IdentityPolicyVersion.V2
                    and any(
                        len(label) > 63
                        for label in self.guest.domain_join.domain.split(".")
                    )
                ):
                    raise ValueError(
                        "Each DNS domain label must not exceed 63 characters."
                    )
                if (
                    self.identity_policy_version == IdentityPolicyVersion.V2
                    and len(f"{short_name}.{self.guest.domain_join.domain}") > 253
                ):
                    raise ValueError("The resulting domain-joined FQDN must not exceed 253 characters.")
            # v1 payloads retain their historical hostname verbatim. v2 and
            # non-domain requests persist the normalized name supplied to Windows.
            if (
                self.guest.domain_join is None
                or self.identity_policy_version == IdentityPolicyVersion.V2
            ):
                self.guest.hostname = short_name.upper()
        return self

    @property
    def effective_computer_name(self) -> str:
        """Short Windows name passed to Rename-Computer, never an FQDN."""
        if self.source_type == VmSourceType.BLANK:
            return ""
        if self.guest.domain_join is not None:
            if self.identity_policy_version == IdentityPolicyVersion.V1:
                return (self.guest.hostname or self.vm.name).upper()
            return self.vm.name.upper()
        return (self.guest.hostname or self.vm.name).upper()

    @property
    def effective_fqdn(self) -> str | None:
        """Canonical DNS identity created by joining the short VM name to AD."""
        if self.source_type == VmSourceType.BLANK or self.guest.domain_join is None:
            return None
        return f"{self.effective_computer_name}.{self.guest.domain_join.domain}".lower()

    @property
    def total_disk_gb(self) -> int:
        return sum(disk.size_gb for disk in self.hardware.disks)


class ProvisioningSubmissionRequest(ProvisioningRequest):
    """Public validation/submission body; historical v1 is read-only."""

    identity_policy_version: Literal[IdentityPolicyVersion.V2] = IdentityPolicyVersion.V2


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
