"""VMware Tools file-transfer URL and TLS behavior."""

from __future__ import annotations

import ssl

from app.services.guest.vmware_tools import _file_transfer_url, _file_transfer_verify
from app.services.vmware.base import VCenterTarget


def target(*, verify_ssl: bool = True) -> VCenterTarget:
    return VCenterTarget(
        id="vcenter-1",
        name="Production vCenter",
        host="vcenter.internal.example",
        port=443,
        username_secret_ref="vcenter/username",
        password_secret_ref="vcenter/password",
        verify_ssl=verify_ssl,
    )


def test_wildcard_transfer_url_uses_vcenter_hostname_and_keeps_port() -> None:
    actual = _file_transfer_url(
        "https://*:9443/guestFile?id=abc", target()
    )
    assert actual == "https://vcenter.internal.example:9443/guestFile?id=abc"


def test_non_wildcard_transfer_url_is_unchanged() -> None:
    url = "https://esxi01.internal.example/guestFile?id=abc"
    assert _file_transfer_url(url, target()) == url


def test_tls_verification_is_enabled_by_default() -> None:
    assert isinstance(_file_transfer_verify(target()), ssl.SSLContext)
    assert _file_transfer_verify(target(verify_ssl=False)) is False
