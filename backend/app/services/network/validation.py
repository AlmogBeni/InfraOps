"""Pure IPv4 validation helpers.

Shared by schema validation, dry-run preflight checks and unit tests. IPv6
support can be added later behind the same function surface.
"""

from __future__ import annotations

import ipaddress
from typing import NamedTuple


class IpValidationIssue(NamedTuple):
    field: str
    message: str


def mask_to_prefix(subnet_mask: str) -> int | None:
    """Convert a dotted-decimal subnet mask to a prefix length (or None)."""
    try:
        return bin(int(ipaddress.IPv4Address(subnet_mask))).count("1") if _is_contiguous_mask(subnet_mask) else None
    except (ValueError, ipaddress.AddressValueError):
        return None


def _is_contiguous_mask(subnet_mask: str) -> bool:
    try:
        packed = int(ipaddress.IPv4Address(subnet_mask))
    except (ValueError, ipaddress.AddressValueError):
        return False
    binary = format(packed, "032b")
    return binary == "1" * binary.count("1") + "0" * (32 - binary.count("1"))


def prefix_to_mask(prefix: int) -> str:
    return str(ipaddress.ip_network(f"0.0.0.0/{prefix}").netmask)


def validate_static_ipv4(
    address: str,
    prefix: int,
    gateway: str,
    dns_servers: list[str],
) -> list[IpValidationIssue]:
    """Return every validation problem found (empty list = valid)."""
    issues: list[IpValidationIssue] = []

    try:
        addr = ipaddress.IPv4Address(address.strip())
    except (ValueError, ipaddress.AddressValueError):
        return [IpValidationIssue("address", f"'{address}' is not a valid IPv4 address.")]

    if not 8 <= prefix <= 32:
        issues.append(IpValidationIssue("prefix", f"Prefix /{prefix} is outside the supported 8–32 range."))

    try:
        gw = ipaddress.IPv4Address(gateway.strip())
    except (ValueError, ipaddress.AddressValueError):
        issues.append(IpValidationIssue("gateway", f"'{gateway}' is not a valid IPv4 address."))
        gw = None

    if 8 <= prefix <= 32:
        network = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
        if addr == network.network_address:
            issues.append(IpValidationIssue("address", "The address equals the network address."))
        if addr == network.broadcast_address:
            issues.append(IpValidationIssue("address", "The address equals the broadcast address."))
        if gw is not None:
            if gw not in network:
                issues.append(
                    IpValidationIssue(
                        "gateway",
                        f"Gateway {gw} is outside the {network.with_prefixlen} subnet.",
                    )
                )
            elif gw in (network.network_address, network.broadcast_address):
                issues.append(IpValidationIssue("gateway", "Gateway cannot be the network or broadcast address."))
            if gw == addr:
                issues.append(IpValidationIssue("gateway", "Gateway must differ from the VM's own address."))

    seen_dns: set[str] = set()
    for index, dns in enumerate(dns_servers, start=1):
        try:
            normalized = str(ipaddress.IPv4Address(dns.strip()))
        except (ValueError, ipaddress.AddressValueError):
            issues.append(IpValidationIssue(f"dns_servers[{index}]", f"'{dns}' is not a valid IPv4 address."))
            continue
        if normalized in ("0.0.0.0", "255.255.255.255"):
            issues.append(IpValidationIssue(f"dns_servers[{index}]", f"'{dns}' is not a usable DNS server address."))
        if normalized in seen_dns:
            issues.append(IpValidationIssue(f"dns_servers[{index}]", f"Duplicate DNS server '{dns}'."))
        seen_dns.add(normalized)

    return issues


def describe_subnet(address: str, prefix: int) -> str:
    try:
        return ipaddress.ip_network(f"{address}/{prefix}", strict=False).with_prefixlen
    except (ValueError, ipaddress.AddressValueError):
        return f"{address}/{prefix}"
