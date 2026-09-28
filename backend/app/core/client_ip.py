"""Trusted client-IP resolution.

``X-Forwarded-For`` is attacker-controlled unless it was written by a proxy
we trust. The address is taken from the header only when the direct peer is
inside ``TRUSTED_PROXY_CIDRS``, and then from the *rightmost* untrusted hop
(the value our own proxy appended), never from the client-supplied leftmost
entry. The bundled nginx overwrites the header with the address it resolved
itself (see frontend/nginx/default.conf.template).
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from app.core.config import get_settings

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def _parse_networks(raw: str) -> tuple[_Network, ...]:
    networks: list[_Network] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if entry:
            networks.append(ipaddress.ip_network(entry, strict=False))
    return tuple(networks)


def _is_trusted(address: str, networks: tuple[_Network, ...]) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def _valid_ip(value: str) -> str | None:
    candidate = value.strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted_cidrs: str) -> str | None:
    """Pure resolution logic (unit-tested)."""
    networks = _parse_networks(trusted_cidrs)
    if peer is None:
        return None
    if not forwarded_for or not _is_trusted(peer, networks):
        return peer
    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    # Walk from the right: skip our own trusted proxies, return the first
    # address that was appended by a trusted hop on behalf of a client.
    for hop in reversed(hops):
        address = _valid_ip(hop)
        if address is None:
            return peer
        if not _is_trusted(address, networks):
            return address
    return _valid_ip(hops[0]) if hops else peer


def client_ip_from_request(request) -> str | None:
    peer = request.client.host if request.client else None
    return resolve_client_ip(
        peer, request.headers.get("x-forwarded-for"), get_settings().trusted_proxy_cidrs
    )
