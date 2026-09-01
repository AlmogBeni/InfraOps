"""Integration tests against the fully-mocked infrastructure providers.

These exercise the same service interfaces the production adapters implement —
discovery, cloning, guest automation, certificate deployment and application
installation — without requiring vCenter or Windows.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.schemas.provisioning import AdapterType, DiskProvisioning, DiskSpec
from app.services.certificates.deployer import CertificateDeployer, CertificateToDeploy
from app.services.guest.mock import MockGuestOperations, mock_guest_state, reset_mock_guests
from app.services.vmware.base import BlankVmSpec, CloneSpec
from app.services.vmware.mock import (
    DEFAULT_MOCK_VCENTER_ID,
    MockVMwareService,
    get_mock_inventory,
    reset_mock_estates,
)


@pytest.fixture(autouse=True)
def clean_mock_state():
    reset_mock_estates()
    reset_mock_guests()
    yield
    reset_mock_estates()
    reset_mock_guests()


@pytest.fixture
def target():
    from app.services.vmware.base import VCenterTarget

    return VCenterTarget(
        id=DEFAULT_MOCK_VCENTER_ID, name="Mock Production vCenter",
        host="vcsa-prod.company.local", port=443,
        username_secret_ref="vcsa-prod/username",
        password_secret_ref="vcsa-prod/password",
        verify_ssl=True,
    )


@pytest.fixture
def credentials():
    from app.services.guest.base import GuestCredentials

    return GuestCredentials(username="administrator", password="not-a-real-password")


@pytest.mark.asyncio
class TestDiscovery:
    async def test_datacenters_listed(self, target):
        service = MockVMwareService()
        datacenters = await service.get_datacenters(target)
        assert {dc.name for dc in datacenters} >= {"DC01-Corporate", "DC02-Lab"}

    async def test_clusters_scoped_to_datacenter(self, target):
        service = MockVMwareService()
        clusters = await service.get_clusters(target, "datacenter-21")
        names = {c.name for c in clusters}
        assert "PROD-CLUSTER" in names and "LAB-CLUSTER" not in names

    async def test_maintenance_host_disabled_for_provisioning(self, target):
        service = MockVMwareService()
        hosts = await service.get_hosts(target, "domain-c7")
        by_name = {h.name: h for h in hosts}
        assert by_name["esx03.company.local"].available_for_provisioning is False
        assert by_name["esx01.company.local"].available_for_provisioning is True

    async def test_templates_are_ovf_ova_only_and_scoped_to_datacenter(self, target):
        service = MockVMwareService()
        primary = await service.get_templates(target, "datacenter-21")
        lab = await service.get_templates(target, "datacenter-22")
        assert {template.id for template in primary} == {
            "ovf-corp-windows-2022",
            "ova-corp-windows-2025",
        }
        assert {template.id for template in lab} == {"ovf-lab-integration"}
        assert {template.type for template in primary + lab} == {"OVF", "OVA"}

    async def test_networks_are_strictly_scoped_to_datacenter(self, target):
        service = MockVMwareService()
        primary = await service.get_networks(target, "datacenter-21")
        lab = await service.get_networks(target, "datacenter-22")
        assert {network.id for network in primary} == {
            "dvportgroup-51",
            "dvportgroup-52",
            "dvportgroup-53",
        }
        assert {network.id for network in lab} == {"network-54"}
        assert {network.id for network in primary}.isdisjoint(network.id for network in lab)

    async def test_isos_are_strictly_scoped_to_datacenter(self, target):
        service = MockVMwareService()
        primary = await service.get_isos(target, "datacenter-21")
        lab = await service.get_isos(target, "datacenter-22")
        assert {image.id for image in primary} == {"iso-corp-windows-2025"}
        assert {image.id for image in lab} == {"iso-lab-ubuntu-2404"}
        assert primary[0].datastore_id == "datastore-41"

    async def test_duplicate_names_detected(self, target):
        service = MockVMwareService()
        assert await service.vm_exists(target, "APP-PROD-004") is True
        assert await service.vm_exists(target, "BRAND-NEW-001") is False


@pytest.mark.asyncio
class TestLifecycle:
    async def make_spec(self, name="TEST-VM-001"):
        return CloneSpec(
            template_id="ova-corp-windows-2025", vm_name=name,
            datacenter_id="datacenter-21", cluster_id="domain-c7",
            host_id="host-11", cpu=4, memory_mb=16384,
            disks=(DiskSpec(size_gb=100, provisioning=DiskProvisioning.THIN),),
            network_id="dvportgroup-51", adapter_type=AdapterType.VMXNET3,
        )

    async def test_clone_registers_vm_and_consumes_capacity(self, target):
        service = MockVMwareService()
        before = {d.id: d.free_gb for d in await service.get_datastores(target, "domain-c7")}
        ref = await service.clone_from_template(target, await self.make_spec())
        after = {d.id: d.free_gb for d in await service.get_datastores(target, "domain-c7")}
        consumed = sum(b - a for b, a in zip(before.values(), after.values(), strict=False))
        assert consumed == 100
        assert await service.vm_exists(target, ref.name)

    async def test_duplicate_clone_rejected(self, target):
        service = MockVMwareService()
        spec = await self.make_spec("APP-PROD-004")
        from app.core.errors import InfraOperationError

        with pytest.raises(InfraOperationError) as excinfo:
            await service.clone_from_template(target, spec)
        assert "already exists" in excinfo.value.human_message

    async def test_create_blank_vm_without_template(self, target):
        service = MockVMwareService()
        spec = BlankVmSpec(
            vm_name="BLANK-VM-001",
            datacenter_id="datacenter-21",
            cluster_id="domain-c7",
            host_id="host-11",
            cpu=2,
            memory_mb=8192,
            disks=(DiskSpec(size_gb=40, provisioning=DiskProvisioning.THIN),),
        )
        ref = await service.create_blank_vm(target, spec)
        info = await service.get_vm_info(target, ref.name)
        assert info is not None
        assert info.power_state == "poweredOff"
        assert info.tools_status is None

    async def test_create_blank_vm_mounts_selected_datacenter_iso(self, target):
        service = MockVMwareService()
        spec = BlankVmSpec(
            vm_name="BLANK-VM-ISO-001",
            datacenter_id="datacenter-21",
            cluster_id="domain-c7",
            host_id="host-11",
            cpu=2,
            memory_mb=8192,
            disks=(DiskSpec(size_gb=40, provisioning=DiskProvisioning.THIN),),
            iso_id="iso-corp-windows-2025",
        )
        ref = await service.create_blank_vm(target, spec)
        vm = get_mock_inventory(target.id).find_vm_by_name(ref.name)
        assert vm is not None
        assert vm.iso_id == "iso-corp-windows-2025"

    async def test_create_blank_vm_rejects_iso_from_another_datacenter(self, target):
        from app.core.errors import InfraOperationError

        service = MockVMwareService()
        spec = BlankVmSpec(
            vm_name="BLANK-VM-ISO-002",
            datacenter_id="datacenter-21",
            cluster_id="domain-c7",
            disks=(DiskSpec(size_gb=40, provisioning=DiskProvisioning.THIN),),
            iso_id="iso-lab-ubuntu-2404",
        )
        with pytest.raises(InfraOperationError, match="selected ISO"):
            await service.create_blank_vm(target, spec)

    async def test_network_attachment_rejects_cross_datacenter_selection(self, target):
        from app.core.errors import InfraOperationError

        service = MockVMwareService()
        spec = BlankVmSpec(
            vm_name="BLANK-VM-NET-001",
            datacenter_id="datacenter-21",
            cluster_id="domain-c7",
            disks=(DiskSpec(size_gb=40, provisioning=DiskProvisioning.THIN),),
        )
        ref = await service.create_blank_vm(target, spec)
        with pytest.raises(InfraOperationError, match="another datacenter"):
            await service.attach_network(
                target,
                ref.id,
                "network-54",
                AdapterType.VMXNET3,
                "datacenter-21",
            )

    async def test_tools_become_ready_after_power_on(self, target):
        service = MockVMwareService()
        ref = await service.clone_from_template(target, await self.make_spec())
        await service.power_on(target, ref.id)
        await service.wait_for_tools(target, ref.id, timeout_seconds=15)
        info = await service.get_vm_info(target, ref.name)
        assert info.tools_status == "toolsOk"

    async def test_resolve_vm_id_roundtrip(self, target):
        service = MockVMwareService()
        ref = await service.clone_from_template(target, await self.make_spec())
        assert await service.resolve_vm_id(target, ref.name) == ref.id
        assert await service.resolve_vm_id(target, "GHOST-VM") is None


@pytest.mark.asyncio
class TestGuestAutomation:
    async def test_static_ip_recorded_and_synced(self, target, credentials):
        from app.workers.stages import build_static_ip_script

        service = MockVMwareService()
        ref = await service.clone_from_template(
            target,
            CloneSpec(
                template_id="ova-corp-windows-2025",
                vm_name="NET-VM-001",
                datacenter_id="datacenter-21",
                cluster_id="domain-c7",
            ),
        )
        await service.power_on(target, ref.id)
        await service.wait_for_tools(target, ref.id, timeout_seconds=15)

        guest = MockGuestOperations()
        script = build_static_ip_script("10.20.30.45", 24, "10.20.30.1", ["10.20.1.10"])
        result = await guest.run_program(
            target, "NET-VM-001", credentials,
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            f"-NoProfile -NonInteractive -Command {script}", 30,
        )
        assert result.succeeded
        state = mock_guest_state("NET-VM-001")
        assert state.ip_address == "10.20.30.45"
        inventory_ip = (await service.get_used_ips(target)).get("10.20.30.45")
        assert inventory_ip == "NET-VM-001"

    async def test_certificate_deploy_idempotent(self, target, credentials):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")]))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
            .not_valid_after(dt.datetime.now(dt.UTC) + dt.timedelta(days=365))
            .sign(key, hashes.SHA256())
        )
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        meta_fingerprint = (
            __import__("hashlib").sha256(
                cert.public_bytes(
                    __import__(
                        "cryptography.hazmat.primitives.serialization",
                        fromlist=["Encoding"],
                    ).Encoding.DER
                )
            ).hexdigest().upper()
        )

        to_deploy = CertificateToDeploy(
            friendly_name="Test Root CA", certificate_type="ROOT",
            thumbprint=meta_fingerprint, pem_body=pem,
            not_after=(dt.date.today() + dt.timedelta(days=365)),
        )
        deployer = CertificateDeployer(MockGuestOperations())
        first = await deployer.deploy(target, "CERT-VM-001", credentials, [to_deploy])
        assert first[0].action == "INSTALLED"
        assert first[0].verified is True

        second = await deployer.deploy(target, "CERT-VM-001", credentials, [to_deploy])
        assert second[0].action == "ALREADY_PRESENT"

    async def test_expired_certificate_refused(self, target, credentials):
        expired = CertificateToDeploy(
            friendly_name="Old CA", certificate_type="ROOT",
            thumbprint="AB" * 32, pem_body="-----BEGIN CERTIFICATE-----x",
            not_after=dt.date.today() - dt.timedelta(days=1),
        )
        deployer = CertificateDeployer(MockGuestOperations())
        records = await deployer.deploy(target, "CERT-VM-002", credentials, [expired])
        assert records[0].action == "FAILED"
        assert "expired" in records[0].detail.lower()

    async def test_failure_hook_produces_failed_result(self, target, credentials):
        guest = MockGuestOperations()
        result = await guest.run_program(
            target, "FAIL-VM", credentials,
            r"C:\Windows\System32\msiexec.exe", "/i evil.msi __FAIL__ /qn", 30,
        )
        assert result.exit_code == 1603
        assert not result.succeeded
