"""Validation tests for the provisioning request schema."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.v1.provisioning import _normalized_request_payload
from app.core.errors import DomainValidationError
from app.schemas.provisioning import (
    FirmwareType,
    HardwareSpec,
    IpMode,
    NetworkSpec,
    ProvisioningRequest,
    ProvisioningSubmissionRequest,
    VmSourceType,
)
from app.services.provisioning.service import submit_provisioning
from app.workers.context import load_request_payload
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

    def test_domain_join_uses_vm_name_for_short_name_and_fqdn(self):
        payload = make_request().model_dump(mode="json")
        payload["vm"]["name"] = "srvildc55"
        payload["guest"]["hostname"] = "IGNORED-CUSTOM-NAME"
        payload["guest"]["domain_join"] = {
            "domain": " Corp.DeltaGalil.com. ",
            "ou": None,
            "credential_secret_ref": "domain-join",
        }

        rebuilt = ProvisioningRequest.model_validate(payload)

        assert rebuilt.guest.hostname == "SRVILDC55"
        assert rebuilt.guest.domain_join is not None
        assert rebuilt.guest.domain_join.domain == "corp.deltagalil.com"
        assert rebuilt.effective_computer_name == "SRVILDC55"
        assert rebuilt.effective_fqdn == "srvildc55.corp.deltagalil.com"
        assert rebuilt.identity_policy_version == "v2"
        assert rebuilt.model_dump(mode="json")["identity_policy_version"] == "v2"

    def test_explicit_v1_preserves_historical_guest_hostname(self):
        payload = make_request().model_dump(mode="json")
        payload["identity_policy_version"] = "v1"
        payload["vm"]["name"] = "NEW-INVENTORY-NAME"
        payload["guest"]["hostname"] = "Legacy-Ad-Name"
        payload["guest"]["domain_join"] = {
            "domain": "corp.example.com",
            "ou": None,
            "credential_secret_ref": "Domain-Join",
        }

        rebuilt = ProvisioningRequest.model_validate(payload)

        assert rebuilt.guest.hostname == "Legacy-Ad-Name"
        assert rebuilt.effective_computer_name == "LEGACY-AD-NAME"
        assert rebuilt.effective_fqdn == "legacy-ad-name.corp.example.com"

    @pytest.mark.parametrize("label_length", [63])
    def test_domain_join_accepts_dns_label_at_boundary(self, label_length):
        payload = make_request().model_dump(mode="json")
        payload["guest"]["domain_join"] = {
            "domain": f"{'a' * label_length}.example.com",
            "ou": None,
            "credential_secret_ref": "domain-join",
        }

        rebuilt = ProvisioningRequest.model_validate(payload)

        assert rebuilt.guest.domain_join is not None
        assert rebuilt.guest.domain_join.domain.startswith("a" * 63)

    def test_domain_join_rejects_dns_label_over_boundary(self):
        payload = make_request().model_dump(mode="json")
        payload["guest"]["domain_join"] = {
            "domain": f"{'a' * 64}.example.com",
            "ou": None,
            "credential_secret_ref": "domain-join",
        }

        with pytest.raises(ValidationError, match="DNS domain label"):
            ProvisioningRequest.model_validate(payload)

    @pytest.mark.parametrize(
        "credential_ref",
        [
            "/domain-join",
            "domain-join/",
            "domain-join//username",
            "domain-join/./username",
            "domain-join/../username",
            "Domain-Join/username",
        ],
    )
    def test_domain_join_rejects_unsafe_credential_reference(self, credential_ref):
        payload = make_request().model_dump(mode="json")
        payload["guest"]["domain_join"] = {
            "domain": "corp.example.com",
            "ou": None,
            "credential_secret_ref": credential_ref,
        }

        with pytest.raises(ValidationError):
            ProvisioningRequest.model_validate(payload)

    def test_domain_join_accepts_safe_slash_separated_credential_reference(self):
        payload = make_request().model_dump(mode="json")
        payload["guest"]["domain_join"] = {
            "domain": "corp.example.com",
            "ou": None,
            "credential_secret_ref": "active-directory/domain-join",
        }

        rebuilt = ProvisioningRequest.model_validate(payload)

        assert rebuilt.guest.domain_join is not None
        assert rebuilt.guest.domain_join.credential_secret_ref == "active-directory/domain-join"

    def test_public_submission_rejects_explicit_v1_policy(self):
        payload = make_request().model_dump(mode="json")
        payload["identity_policy_version"] = "v1"

        with pytest.raises(ValidationError, match="v2"):
            ProvisioningSubmissionRequest.model_validate(payload)

    @pytest.mark.asyncio
    async def test_service_boundary_rejects_v1_request(self):
        payload = make_request().model_dump(mode="json")
        payload["identity_policy_version"] = "v1"
        request = ProvisioningRequest.model_validate(payload)

        with pytest.raises(DomainValidationError, match="reserved for stored legacy jobs"):
            await submit_provisioning(
                object(),
                user=None,
                request=request,
                idempotency_key=None,
                source_ip=None,
            )

    @pytest.mark.parametrize("bad_name", ["server.prod", "123456"])
    def test_domain_join_rejects_names_invalid_for_rename_computer(self, bad_name):
        payload = make_request().model_dump(mode="json")
        payload["vm"]["name"] = bad_name
        payload["guest"]["domain_join"] = {
            "domain": "corp.example.com",
            "ou": None,
            "credential_secret_ref": "domain-join",
        }

        with pytest.raises(ValidationError, match="valid Windows computer name"):
            ProvisioningRequest.model_validate(payload)

    def test_legacy_request_defaults_to_template_source(self):
        payload = make_request().model_dump(mode="json")
        payload.pop("source_type")
        rebuilt = ProvisioningRequest.model_validate(payload)
        assert rebuilt.source_type == VmSourceType.TEMPLATE

    def test_legacy_job_payload_is_normalized_for_current_readers(self):
        payload = make_request().model_dump(mode="json")
        payload.pop("source_type")
        payload.pop("identity_policy_version")
        payload["guest"].pop("iso_id")
        payload["vm"]["name"] = "NEW-INVENTORY-NAME"
        payload["guest"]["hostname"] = "LEGACY-AD-NAME"
        payload["guest"]["domain_join"] = {
            "domain": "corp.example.com",
            "ou": None,
            "credential_secret_ref": "Domain-Join",
        }

        normalized = _normalized_request_payload(payload)

        assert normalized is not None
        assert normalized["source_type"] == "template"
        assert normalized["identity_policy_version"] == "v1"
        assert normalized["guest"]["iso_id"] is None
        assert normalized["guest"]["hostname"] == "LEGACY-AD-NAME"
        assert normalized["guest"]["domain_join"]["credential_secret_ref"] == "Domain-Join"

    def test_v1_reader_preserves_pre_policy_identity_and_reference_shapes(self):
        payload = make_request().model_dump(mode="json")
        payload["identity_policy_version"] = "v1"
        payload["guest"]["hostname"] = "legacy.host"
        payload["guest"]["domain_join"] = {
            "domain": f"{'a' * 64}.example.com",
            "ou": None,
            "credential_secret_ref": "Domain-Join",
        }

        rebuilt = ProvisioningRequest.model_validate(payload)

        assert rebuilt.guest.hostname == "legacy.host"
        assert rebuilt.guest.domain_join is not None
        assert rebuilt.guest.domain_join.credential_secret_ref == "Domain-Join"

    @pytest.mark.asyncio
    async def test_worker_load_marks_unversioned_payload_v1(self):
        payload = make_request().model_dump(mode="json")
        payload.pop("identity_policy_version")
        payload["vm"]["name"] = "NEW-INVENTORY-NAME"
        payload["guest"]["hostname"] = "LEGACY-AD-NAME"
        payload["guest"]["domain_join"] = {
            "domain": "corp.example.com",
            "ou": None,
            "credential_secret_ref": "Domain-Join",
        }
        job = SimpleNamespace(id="legacy-job", request=SimpleNamespace(payload=payload))

        rebuilt = await load_request_payload(job)

        assert rebuilt.identity_policy_version == "v1"
        assert rebuilt.guest.hostname == "LEGACY-AD-NAME"
        assert rebuilt.guest.domain_join is not None
        assert rebuilt.guest.domain_join.credential_secret_ref == "Domain-Join"
        assert rebuilt.effective_computer_name == "LEGACY-AD-NAME"
        assert rebuilt.effective_fqdn == "legacy-ad-name.corp.example.com"

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
            "iso_id": None,
            "hostname": None,
            "timezone": None,
            "domain_join": None,
        }
        rebuilt = ProvisioningRequest.model_validate(payload)
        assert rebuilt.source_type == VmSourceType.BLANK
        assert rebuilt.guest.template_id is None
        assert rebuilt.guest.iso_id is None

    def test_blank_source_accepts_selected_iso(self):
        payload = make_request().model_dump(mode="json")
        payload["source_type"] = "blank"
        payload["guest"] = {
            "template_id": None,
            "iso_id": "iso-corp-windows-2025",
            "hostname": None,
            "timezone": None,
            "domain_join": None,
        }
        rebuilt = ProvisioningRequest.model_validate(payload)
        assert rebuilt.guest.iso_id == "iso-corp-windows-2025"

    def test_template_source_rejects_iso(self):
        payload = make_request().model_dump(mode="json")
        payload["guest"]["iso_id"] = "iso-corp-windows-2025"
        with pytest.raises(ValidationError, match="iso_id must be null"):
            ProvisioningRequest.model_validate(payload)

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
