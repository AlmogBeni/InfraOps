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

    # Windows Server installation ISO on a datastore (see inventory_refs).
    iso_id: str = Field(min_length=1, max_length=2048)
    hostname: str | None = Field(default=None, max_length=64, pattern=VM_NAME_PATTERN.pattern)
    timezone: str | None = Field(default=None, max_length=100)
    installation_locale: str = Field(default="en-US", min_length=2, max_length=35)
    input_locale: str = Field(default="0409:00000409", min_length=2, max_length=100)
    # Edition inside install.wim; see DESKTOP_EXPERIENCE_EDITIONS. Stored jobs
    # may hold any index, new requests only a Desktop Experience edition.
    windows_image_index: int = Field(default=2, ge=1, le=99)
    credential_secret_ref: str = Field(default="guest-local-admin", min_length=2, max_length=150)
    # Optional credential (purpose windows_product_key) whose password is the
    # Windows product key, for media that asks for one (retail / MAK).
    product_key_secret_ref: str | None = Field(
        default=None, min_length=2, max_length=150, pattern=SECRET_REFERENCE_PATTERN.pattern
    )
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

    # Every VM is a blank VM installed from a Windows ISO; the field is kept so
    # a client naming any other source gets an explicit error.
    source_type: Literal["blank"] = "blank"
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

    @field_validator("source_type", mode="before")
    @classmethod
    def _only_blank_vms(cls, value: object) -> object:
        if value not in (None, "blank"):
            raise ValueError(
                "source_type must be 'blank': every VM is installed from a Windows ISO."
            )
        return "blank" if value is None else value

    @model_validator(mode="after")
    def _validate_identity(self) -> ProvisioningRequest:
        if (
            self.identity_policy_version == IdentityPolicyVersion.V2
            and not SECRET_REFERENCE_PATTERN.fullmatch(self.guest.credential_secret_ref)
        ):
            raise ValueError(
                "guest.credential_secret_ref must be a safe lowercase, slash-separated secret reference."
            )
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
        if self.guest.domain_join is not None:
            if self.identity_policy_version == IdentityPolicyVersion.V1:
                return (self.guest.hostname or self.vm.name).upper()
            return self.vm.name.upper()
        return (self.guest.hostname or self.vm.name).upper()

    @property
    def effective_fqdn(self) -> str | None:
        """Canonical DNS identity created by joining the short VM name to AD."""
        if self.guest.domain_join is None:
            return None
        return f"{self.effective_computer_name}.{self.guest.domain_join.domain}".lower()

    @property
    def total_disk_gb(self) -> int:
        return sum(disk.size_gb for disk in self.hardware.disks)


# Microsoft's Windows Server media (2016-2025, evaluation, volume and retail)
# list their editions in this order: 1 Standard and 3 Datacenter are Server
# Core, 2 and 4 the same editions with the Desktop Experience.
DESKTOP_EXPERIENCE_EDITIONS: dict[int, str] = {
    2: "Standard (Desktop Experience)",
    4: "Datacenter (Desktop Experience)",
}


def parse_stored_request(payload: object) -> ProvisioningRequest | None:
    """Read a stored request with the current contract; None for legacy shapes.

    Jobs written by workflows that no longer exist (another VM source, or a
    blank VM without installation media) stay readable as raw data but can
    no longer be executed.
    """
    if not isinstance(payload, dict):
        return None
    candidate = dict(payload)
    # Jobs written before identity policy versioning used guest.hostname as
    # their Windows/AD identity. Never reinterpret those rows with v2 rules.
    candidate.setdefault("identity_policy_version", "v1")
    # Earlier releases serialized since-removed guest fields as null; a
    # non-null value means the job used a removed workflow.
    guest = candidate.get("guest")
    if isinstance(guest, dict):
        candidate["guest"] = {
            key: value for key, value in guest.items()
            if value is not None or key in GuestSpec.model_fields
        }
    try:
        return ProvisioningRequest.model_validate(candidate)
    except ValueError:
        return None


class ProvisioningSubmissionRequest(ProvisioningRequest):
    """Public validation/submission body; historical v1 is read-only."""

    identity_policy_version: Literal[IdentityPolicyVersion.V2] = IdentityPolicyVersion.V2

    @model_validator(mode="after")
    def _desktop_experience_only(self) -> ProvisioningSubmissionRequest:
        if self.guest.windows_image_index not in DESKTOP_EXPERIENCE_EDITIONS:
            raise ValueError(
                "guest.windows_image_index must be 2 (Standard) or 4 (Datacenter): every server "
                "is installed with the Desktop Experience."
            )
        return self


class CredentialOptionOut(BaseModel):
    name: str
    purpose: str
    revision: int
    updated_at: str | None = None


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

    @field_validator("address", mode="before")
    @classmethod
    def _normalize_address(cls, value: object) -> object:
        if isinstance(value, str):
            return _validate_ipv4(value, "IP address")
        return value


class IpConflictReport(BaseModel):
    address: str
    prefix: int
    providers: list[ProviderResult]
    conflict_detected: bool
    confidence_note: str
