"""Client IP resolution must not trust client-supplied forwarding headers."""

from __future__ import annotations

from app.core.client_ip import resolve_client_ip

TRUSTED = "172.16.0.0/12"


def test_untrusted_peer_header_is_ignored() -> None:
    assert resolve_client_ip("198.51.100.7", "203.0.113.1", TRUSTED) == "198.51.100.7"


def test_trusted_proxy_uses_rightmost_untrusted_hop_not_client_supplied_leftmost() -> None:
    # Client sent "X-Forwarded-For: 1.2.3.4"; the proxy appended the real address.
    assert resolve_client_ip("172.18.0.5", "1.2.3.4, 198.51.100.7", TRUSTED) == "198.51.100.7"


def test_trusted_proxy_chain_is_skipped() -> None:
    assert resolve_client_ip("172.18.0.5", "198.51.100.7, 172.18.0.1", TRUSTED) == "198.51.100.7"


def test_garbage_header_falls_back_to_peer() -> None:
    assert resolve_client_ip("172.18.0.5", "not-an-ip", TRUSTED) == "172.18.0.5"


def test_no_header_uses_peer() -> None:
    assert resolve_client_ip("172.18.0.5", None, TRUSTED) == "172.18.0.5"
