"""Device layout of a blank VM installed from an ISO (real vSphere adapter, fake inventory)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pyVmomi import vim as real_vim

from app.schemas.provisioning import DiskProvisioning, DiskSpec, FirmwareType
from app.services.vmware import vsphere
from app.services.vmware.base import BlankVmSpec, VCenterTarget
from app.services.vmware.inventory_refs import encode_iso_id


class FakeDatacenter:
    def __init__(self, vm_folder) -> None:
        self.vmFolder = vm_folder
        self.datastoreFolder = object()


class FakeCompute:
    def __init__(self) -> None:
        self.resourcePool = SimpleNamespace(_moId="resgroup-8")
        self.host = []


class FakeDatastore(real_vim.Datastore):
    """A real vim.Datastore type (device backings type-check it) with fixed properties."""

    name = "ds1"
    summary = SimpleNamespace(accessible=True, freeSpace=10 * 1024**4)

    def __init__(self) -> None:
        super().__init__("datastore-1")


class _Vim:
    """Real pyVmomi types, except the inventory classes the fakes stand in for."""

    Datacenter = FakeDatacenter
    ComputeResource = FakeCompute
    Datastore = FakeDatastore

    def __getattr__(self, name):
        return getattr(real_vim, name)


@pytest.mark.asyncio
async def test_iso_install_vm_gets_the_host_tools_iso_on_a_second_drive(monkeypatch) -> None:
    captured: dict = {}
    created = SimpleNamespace(
        _moId="vm-501",
        name="APP-501",
        config=SimpleNamespace(hardware=SimpleNamespace(device=[])),
        ReconfigVM_Task=lambda spec: captured.setdefault("boot", spec),
    )

    def create_vm(config, pool, host):
        captured["config"] = config
        return SimpleNamespace(info=SimpleNamespace(result=created))

    datastore = FakeDatastore()
    datacenter = FakeDatacenter(SimpleNamespace(CreateVM_Task=create_vm))
    compute = FakeCompute()
    view = SimpleNamespace(view=[datastore], Destroy=lambda: None)
    content = SimpleNamespace(
        viewManager=SimpleNamespace(CreateContainerView=lambda *args: view)
    )
    objects = {"datacenter-21": datacenter, "domain-c7": compute}

    monkeypatch.setattr(vsphere, "vim", _Vim())
    service = object.__new__(vsphere.VsphereVMwareService)
    service._content = lambda si: content
    service._find_vms_by_name = lambda found_in, name: []
    service._find_by_moref = lambda found_in, moref: objects.get(moref)
    service._compute_belongs_to_datacenter = lambda *args: True
    service._cluster_datastores = lambda cluster: {datastore._moId: datastore}
    service._wait_for_task = lambda task: None

    async def with_session(target, fn, **kwargs):
        return fn(object())

    service._with_session = with_session

    ref = await service.create_blank_vm(
        VCenterTarget(
            id="vc", name="vc", host="vc.example.test", port=443,
            username_secret_ref="u", password_secret_ref="p", verify_ssl=True,
        ),
        BlankVmSpec(
            vm_name="APP-501",
            datacenter_id="datacenter-21",
            cluster_id="domain-c7",
            disks=(DiskSpec(size_gb=60, provisioning=DiskProvisioning.THIN),),
            firmware=FirmwareType.EFI,
            iso_id=encode_iso_id("datastore-1", "[ds1] iso/windows-server-2025.iso"),
            job_id="job-1",
        ),
    )

    assert ref.id == "vm-501"
    cdroms = {
        change.device.unitNumber: change.device.backing.fileName
        for change in captured["config"].deviceChange
        if isinstance(change.device, real_vim.vm.device.VirtualCdrom)
    }
    # Unit 0 boots Windows Setup; unit 1 supplies VMware Tools at first logon.
    assert cdroms == {
        0: "[ds1] iso/windows-server-2025.iso",
        1: vsphere.HOST_TOOLS_ISO_PATH,
    }
    assert vsphere.HOST_TOOLS_ISO_PATH == "[] /vmimages/tools-isoimages/windows.iso"
