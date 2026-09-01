"""Focused tests for real-vSphere template inventory scoping."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.services.vmware.base import VCenterTarget
from app.services.vmware.vsphere import VsphereVMwareService


class _View:
    def __init__(self, entities: list[object]) -> None:
        self.view = entities
        self.destroyed = False

    def Destroy(self) -> None:  # noqa: N802 - pyVmomi API spelling
        self.destroyed = True


def _inventory_vm(name: str, moref: str, datacenter: object, *, template: bool) -> object:
    vm_folder = datacenter.vmFolder
    nested_folder = SimpleNamespace(parent=vm_folder)
    return SimpleNamespace(
        _moId=moref,
        name=name,
        parent=nested_folder,
        config=SimpleNamespace(
            template=template,
            guestFullName="Microsoft Windows Server 2022 (64-bit)",
            modifyDate=None,
            annotation="Golden image",
            hardware=SimpleNamespace(numCPU=4, memoryMB=8192, device=[]),
        ),
        summary=None,
    )


@pytest.mark.asyncio
async def test_templates_are_discovered_across_datacenters(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    first_dc = SimpleNamespace(_moId="datacenter-21", name="Production")
    first_dc.vmFolder = SimpleNamespace(parent=first_dc)
    second_dc = SimpleNamespace(_moId="datacenter-22", name="Lab")
    second_dc.vmFolder = SimpleNamespace(parent=second_dc)
    first_template = _inventory_vm("WS2022-GOLD", "vm-101", first_dc, template=True)
    second_template = _inventory_vm("LAB-TEMPLATE", "vm-102", second_dc, template=True)
    ordinary_vm = _inventory_vm("RUNNING-VM", "vm-103", first_dc, template=False)
    view = _View([first_template, second_template, ordinary_vm])
    content = SimpleNamespace(
        rootFolder=object(),
        viewManager=SimpleNamespace(CreateContainerView=lambda *_: view),
    )

    service = object.__new__(VsphereVMwareService)
    service._content = lambda _: content
    async def invoke(_, fn, *, operation="session-call"):
        assert operation == "list-templates"
        return fn(object())

    service._with_session = invoke
    target = VCenterTarget(
        id="11111111-1111-4111-8111-111111111111",
        name="vCenter",
        host="vcenter.example.test",
        port=443,
        username_secret_ref="vc-user",
        password_secret_ref="vc-password",
        verify_ssl=True,
    )

    templates = await service.get_templates(target, first_dc._moId)

    assert {template.id for template in templates} == {"vm-101", "vm-102"}
    assert {template.datacenter_id for template in templates} == {first_dc._moId, second_dc._moId}
    assert {template.datacenter_name for template in templates} == {"Production", "Lab"}
    assert view.destroyed is True
    assert "visible_classic_templates=2" in caplog.text
    assert "returned_templates=2" in caplog.text


def test_datacenter_resolution_handles_nested_vm_folders() -> None:
    datacenter = SimpleNamespace(name="Production")
    vm_folder = SimpleNamespace(parent=datacenter)
    datacenter.vmFolder = vm_folder
    nested = SimpleNamespace(parent=vm_folder)
    vm = SimpleNamespace(parent=nested)

    assert VsphereVMwareService._owning_datacenter(vm) is datacenter
