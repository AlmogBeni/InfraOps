"""Focused tests for real-vSphere OVF/OVA Content Library discovery."""

from __future__ import annotations

import contextlib
import logging

import httpx
import pytest

from app.core.errors import InfraOperationError
from app.schemas.infrastructure import TemplateOut
from app.services.vmware.base import CloneSpec, VCenterTarget
from app.services.vmware.content_library import (
    ContentLibraryClient,
    library_item_id,
    package_type,
)
from app.services.vmware.vsphere import VsphereVMwareService


@pytest.fixture
def target() -> VCenterTarget:
    return VCenterTarget(
        id="11111111-1111-4111-8111-111111111111",
        name="vCenter",
        host="vcenter.example.test",
        port=443,
        username_secret_ref="vc-user",
        password_secret_ref="vc-password",
        verify_ssl=True,
    )


def test_package_type_uses_actual_package_files() -> None:
    assert package_type(["appliance.ovf", "disk-1.vmdk"]) == "OVF"
    assert package_type(["appliance.ova"]) == "OVA"
    assert package_type([]) == "OVF"


def test_public_library_item_ids_are_opaque_and_validated() -> None:
    assert library_item_id("library-item:9b6d") == "9b6d"
    with pytest.raises(ValueError):
        library_item_id("vm-101")


@pytest.mark.asyncio
async def test_content_library_discovery_returns_only_real_ovf_items(target) -> None:
    responses = {
        "/content/library": ["library-1"],
        "/content/library/library-1": {"name": "Production Appliances"},
        "/content/library/item?library_id=library-1": ["item-ovf", "item-iso"],
        "/content/library/item/item-ovf": {
            "name": "Payroll Appliance",
            "type": "ovf",
            "description": "Signed payroll service appliance",
            "size": 2048,
            "last_modified_time": "2026-08-31T12:30:00Z",
        },
        "/content/library/item/item-ovf/file": [
            {"name": "payroll.ova", "size": 2048},
        ],
        "/content/library/item/item-iso": {
            "name": "Installer ISO",
            "type": "iso",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.raw_path.decode()
        return httpx.Response(200, json=responses[key])

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://vcenter.example.test",
    )

    @contextlib.asynccontextmanager
    async def fake_client(_target):
        async with http:
            yield http

    client = object.__new__(ContentLibraryClient)
    client._client = fake_client
    templates = await client.list_ovf_packages(target, "datacenter-21")

    assert templates == [
        TemplateOut(
            id="library-item:item-ovf",
            name="Payroll Appliance",
            type="OVA",
            description="Signed payroll service appliance",
            datacenter_id=None,
            datacenter_name=None,
            storage_name=None,
            location="Content Library / Production Appliances / Payroll Appliance",
            size_bytes=2048,
            last_modified="2026-08-31T12:30:00Z",
        )
    ]


@pytest.mark.asyncio
async def test_vsphere_template_discovery_delegates_to_content_library(
    target, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    expected = TemplateOut(
        id="library-item:item-1",
        name="Application Appliance",
        type="OVF",
        description="",
    )

    class FakeContentLibrary:
        async def list_ovf_packages(self, called_target, datacenter_id):
            assert called_target is target
            assert datacenter_id == "datacenter-21"
            return [expected]

    service = object.__new__(VsphereVMwareService)
    service._content_library = FakeContentLibrary()
    templates = await service.get_templates(target, "datacenter-21")

    assert templates == [expected]
    assert "returned_templates=1" in caplog.text


@pytest.mark.asyncio
async def test_real_deployment_rejects_classic_vm_template_ids(target) -> None:
    class FakeContentLibrary:
        @staticmethod
        def is_library_item(value: str) -> bool:
            return value.startswith("library-item:")

    service = object.__new__(VsphereVMwareService)
    service._content_library = FakeContentLibrary()

    with pytest.raises(InfraOperationError, match="not an OVF/OVA"):
        await service.clone_from_template(
            target,
            CloneSpec(
                template_id="vm-101",
                vm_name="SERVER-101",
                datacenter_id="datacenter-21",
            ),
        )


def test_datacenter_resolution_handles_nested_vm_folders() -> None:
    class Entity:
        pass

    datacenter = Entity()
    vm_folder = Entity()
    vm_folder.parent = datacenter
    datacenter.vmFolder = vm_folder
    nested = Entity()
    nested.parent = vm_folder
    vm = Entity()
    vm.parent = nested

    assert VsphereVMwareService._owning_datacenter(vm) is datacenter
