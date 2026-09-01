"""Validation tests for the provisioning request schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.provisioning import (
    FirmwareType,
    HardwareSpec,
    IpMode,
    NetworkSpec,
    ProvisioningRequest,
    VmSourceType,
)
from tests.conftest import make_ipv4, make_request


class TestVmSpec:
    def test_accepts_standard_names(self):
        assert make_request().vm.name == "SERVER-PROD-042"

    @pytest.mark.parametrize("bad", ["-leading-dash", "with space", "x" * 70, ""])
    def test_rejects_bad_names(self, bad):
        payload = make_request().model_dump(mode="json")
        payload["vm"]["name"] = bad
        with pytest.raises(ValidationError):
            ProvisioningRequest.model_validate(payload)

    def test_hostname_defaults_to_vm_name(self):
        payload = make_request().model_dump(mode="json")
        payload["guest"]["hostname"] = None
        rebuilt = ProvisioningRequest.model_validate(payload)
        assert rebuilt.guest.hostname == rebuilt.vm.name

    def test_legacy_request_defaults_to_template_source(self):
        payload = make_request().model_dump(mode="json")
        payload.pop("source_type")
        rebuilt = ProvisioningRequest.model_validate(payload)
        assert rebuilt.source_type == VmSourceType.TEMPLATE

    def test_template_source_requires_template(self):
        payload = make_request().model_dump(mode="json")
        payload["guest"]["template_id"] = None
        with pytest.raises(ValidationError, match="template_id is required"):
            ProvisioningRequest.model_validate(payload)

    def test_blank_source_accepts_no_guest_automation(self):
        payload = make_request().model_dump(mode="json")
        payload["source_type"] = "blank"
        payload["guest"] = {
            "template_id": None,
            "hostname": None,
            "timezone": None,
            "domain_join": None,
        }
        rebuilt = ProvisioningRequest.model_validate(payload)
        assert rebuilt.source_type == VmSourceType.BLANK
        assert rebuilt.guest.template_id is None

    def test_blank_source_rejects_stale_template(self):
        payload = make_request().model_dump(mode="json")
        payload["source_type"] = "blank"
        with pytest.raises(ValidationError, match="template_id must be null"):
            ProvisioningRequest.model_validate(payload)

    def test_legacy_site_id_is_ignored(self):
        payload = make_request().model_dump(mode="json")
        payload["compute"]["site_id"] = "22222222-2222-4222-8222-222222222222"

        rebuilt = ProvisioningRequest.model_validate(payload)

        assert "site_id" not in rebuilt.compute.model_dump()


class TestHardwareSpec:
    def test_secure_boot_requires_efi(self):
        base = make_request().hardware.model_dump()
        base["firmware"] = FirmwareType.BIOS
        base["secure_boot"] = True
        with pytest.raises(ValidationError):
            HardwareSpec.model_validate(base)

    def test_disk_bounds(self):
        with pytest.raises(ValidationError):
            HardwareSpec(
                cpu=2, memory_mb=4096, disks=[{"size_gb": 0, "provisioning": "thin"}]
            )
        with pytest.raises(ValidationError):
            HardwareSpec(cpu=2, memory_mb=4096, disks=[])

    def test_total_disk_helper(self):
        assert make_request().total_disk_gb == 100


class TestNetworkSpec:
    def test_static_requires_ipv4(self):
        with pytest.raises(ValidationError):
            NetworkSpec(network_id="net-1", mode=IpMode.STATIC, ipv4=None)

    def test_dhcp_allows_missing_ipv4(self):
        spec = NetworkSpec(network_id="net-1", mode=IpMode.DHCP, ipv4=None)
        assert spec.mode == IpMode.DHCP

    def test_gateway_must_be_in_subnet(self):
        with pytest.raises(ValidationError):
            make_ipv4(gateway="192.168.99.1")

    def test_gateway_must_differ_from_address(self):
        with pytest.raises(ValidationError):
            make_ipv4(gateway="10.20.30.45")

    def test_network_address_rejected(self):
        with pytest.raises(ValidationError):
            make_ipv4(address="10.20.30.0")

    def test_broadcast_address_rejected(self):
        with pytest.raises(ValidationError):
            make_ipv4(address="10.20.30.255")

    def test_duplicate_dns_rejected(self):
        with pytest.raises(ValidationError):
            make_ipv4(dns_servers=["10.20.1.10", "10.20.1.10"])

    def test_extra_fields_forbidden(self):
        payload = make_request().model_dump(mode="json")
        payload["surprise"] = True
        with pytest.raises(ValidationError):
            ProvisioningRequest.model_validate(payload)
