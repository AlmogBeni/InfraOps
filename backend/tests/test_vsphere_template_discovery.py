"""Focused tests for real-vSphere OVF/OVA Content Library discovery."""

from __future__ import annotations

import contextlib
import json
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


class _FakeSecrets:
    async def get_credentials(self, username_ref: str, password_ref: str) -> tuple[str, str]:
        return "infraops.svc", "not-a-real-password"


def _deploy_spec() -> CloneSpec:
    return CloneSpec(
        template_id="library-item:item-ovf",
        vm_name="APP-501",
        datacenter_id="datacenter-21",
        host_id="host-10",
        datastore_id="datastore-12",
        network_id="dvportgroup-44",
        job_id="job-1",
    )


def _mock_vcenter(monkeypatch: pytest.MonkeyPatch, deploy: httpx.Response) -> list[tuple[str, dict]]:
    """Route the Content Library client's real HTTP calls to a fake vCenter."""
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(201, json="session-token")
        assert request.url.path == "/api/vcenter/ovf/library-item/item-ovf"
        assert request.headers["vmware-api-session-id"] == "session-token"
        action = request.url.params["action"]
        calls.append((action, json.loads(request.content)))
        if action == "filter":
            return httpx.Response(200, json={"networks": ["VM Network"]})
        return deploy

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return calls


@pytest.mark.asyncio
async def test_ovf_deploy_sends_the_rest_api_field_names(target, monkeypatch) -> None:
    calls = _mock_vcenter(
        monkeypatch,
        httpx.Response(
            200, json={"succeeded": True, "resource_id": {"type": "VirtualMachine", "id": "vm-501"}}
        ),
    )

    vm = await ContentLibraryClient(_FakeSecrets()).deploy_ovf_package(
        target, _deploy_spec(), resource_pool_id="resgroup-8"
    )

    assert (vm.id, vm.name) == ("vm-501", "APP-501")
    target_body = {"resource_pool_id": "resgroup-8", "host_id": "host-10"}
    assert calls[0] == ("filter", {"target": target_body})
    action, body = calls[1]
    assert action == "deploy"
    assert body["target"] == target_body
    spec = body["deployment_spec"]
    # The REST field is case-sensitive and required; a wrong spelling is HTTP 400.
    assert spec["accept_all_EULA"] is True
    assert "accept_all_eula" not in spec
    assert spec["name"] == "APP-501"
    assert spec["default_datastore_id"] == "datastore-12"
    assert spec["network_mappings"] == {"VM Network": "dvportgroup-44"}


@pytest.mark.asyncio
async def test_ovf_deploy_rejection_reports_what_vcenter_said(target, monkeypatch) -> None:
    vcenter_message = (
        "Structure 'com.vmware.vcenter.ovf.library_item.resource_pool_deployment_spec' "
        "is missing a field: accept_all_EULA"
    )
    _mock_vcenter(
        monkeypatch,
        httpx.Response(
            400,
            json={
                "error_type": "INVALID_ARGUMENT",
                "messages": [
                    {
                        "args": [],
                        "default_message": vcenter_message,
                        "id": "vapi.bindings.typeconverter.dict.missing.key",
                    }
                ],
            },
        ),
    )

    with pytest.raises(InfraOperationError) as caught:
        await ContentLibraryClient(_FakeSecrets()).deploy_ovf_package(
            target, _deploy_spec(), resource_pool_id="resgroup-8"
        )

    error = caught.value
    assert "rejected the OVF/OVA deployment request" in error.human_message
    # vCenter's explanation is administrator diagnostics, not operator text.
    assert f"INVALID_ARGUMENT: {vcenter_message}" in error.technical_detail
    assert vcenter_message not in error.reason
    assert "read permissions" not in error.recommended_action
    assert error.retryable is False
