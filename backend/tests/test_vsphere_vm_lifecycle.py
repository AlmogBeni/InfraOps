"""VM devices for an unattended ISO install (real vSphere adapter, fake inventory)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pyVmomi import vim as real_vim

from app.schemas.provisioning import DiskProvisioning, DiskSpec, FirmwareType
from app.services.vmware import vsphere
from app.services.vmware.base import VCenterTarget, VmCreateSpec
from app.services.vmware.inventory_refs import encode_iso_id

devices = real_vim.vm.device
TARGET = VCenterTarget(
    id="vc", name="vc", host="vc.example.test", port=443,
    username_secret_ref="u", password_secret_ref="p", verify_ssl=True,
)


class FakeDatacenter:
    def __init__(self, vm_folder=None, name: str = "DC01", parent=None) -> None:
        self.vmFolder = vm_folder
        self.datastoreFolder = object()
        self.name = name
        self.parent = parent


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


class FakeTask:
    def __init__(self, result=None, *, running_until=None) -> None:
        self._result = result
        self._running_until = running_until or (lambda: True)

    @property
    def info(self):
        done = self._running_until()
        state = real_vim.TaskInfo.State.success if done else real_vim.TaskInfo.State.running
        return SimpleNamespace(state=state, result=self._result, error=None)


def service_for(monkeypatch, objects: dict, content=None) -> vsphere.VsphereVMwareService:
    monkeypatch.setattr(vsphere, "vim", _Vim())
    monkeypatch.setattr(vsphere, "_TASK_POLL_INTERVAL", 0.0)
    service = object.__new__(vsphere.VsphereVMwareService)
    service._content = lambda si: content or SimpleNamespace()
    service._find_vms_by_name = lambda found_in, name: []
    service._find_by_moref = lambda found_in, moref: objects.get(moref)
    service._compute_belongs_to_datacenter = lambda *args: True
    service._wait_for_task = lambda task: None

    async def with_session(target, fn, **kwargs):
        return fn(SimpleNamespace(_stub=SimpleNamespace(cookie='vmware_soap_session="abc"')))

    service._with_session = with_session
    return service


def fake_vm(device_list: list, **extra) -> SimpleNamespace:
    vm = SimpleNamespace(
        _moId="vm-501",
        name="APP-501",
        config=SimpleNamespace(hardware=SimpleNamespace(device=device_list)),
        runtime=SimpleNamespace(question=None),
        datastore=[FakeDatastore()],
        reconfigured=[],
    )

    def reconfigure(spec):
        vm.reconfigured.append(spec)
        return FakeTask()

    vm.ReconfigVM_Task = reconfigure
    vm.__dict__.update(extra)
    return vm


def create_spec(firmware: FirmwareType) -> VmCreateSpec:
    return VmCreateSpec(
        vm_name="APP-501",
        datacenter_id="datacenter-21",
        cluster_id="domain-c7",
        iso_id=encode_iso_id("datastore-1", "[ds1] iso/windows-server-2025.iso"),
        os_disk=DiskSpec(size_gb=60, provisioning=DiskProvisioning.THIN),
        firmware=firmware,
        secure_boot=firmware == FirmwareType.EFI,
        job_id="job-1",
    )


async def _create(monkeypatch, firmware: FirmwareType):
    captured: dict = {}
    os_disk = devices.VirtualDisk(key=2000)
    created = fake_vm([os_disk])

    def create_vm(config, pool, host):
        captured["config"] = config
        return FakeTask(created)

    datastore = FakeDatastore()
    datacenter = FakeDatacenter(SimpleNamespace(CreateVM_Task=create_vm))
    view = SimpleNamespace(view=[datastore], Destroy=lambda: None)
    content = SimpleNamespace(viewManager=SimpleNamespace(CreateContainerView=lambda *args: view))
    service = service_for(monkeypatch, {"datacenter-21": datacenter, "domain-c7": FakeCompute()}, content)
    service._cluster_datastores = lambda cluster: {datastore._moId: datastore}

    ref = await service.create_vm(TARGET, create_spec(firmware))
    return ref, captured["config"], created


@pytest.mark.parametrize("firmware", [FirmwareType.EFI, FirmwareType.BIOS])
async def test_vm_is_created_with_one_disk_on_an_inbox_driver_controller(monkeypatch, firmware) -> None:
    ref, config, created = await _create(monkeypatch, firmware)

    assert ref.id == "vm-501"
    assert config.guestId == "windows9Server64Guest"
    assert (config.firmware == "efi") is (firmware == FirmwareType.EFI)
    added = [change.device for change in config.deviceChange]
    # Windows Setup has an inbox LSI Logic SAS driver; PVSCSI would need injected drivers.
    controllers = [device for device in added if isinstance(device, devices.VirtualSCSIController)]
    assert [type(controller) for controller in controllers] == [devices.VirtualLsiLogicSASController]
    disks = [device for device in added if isinstance(device, devices.VirtualDisk)]
    assert len(disks) == 1  # only the OS disk exists during Setup, so it is disk 0
    assert disks[0].unitNumber == 0 and disks[0].controllerKey == controllers[0].key
    assert disks[0].capacityInKB == 60 * 1024 * 1024
    cdroms = {
        device.unitNumber: device.backing.fileName
        for device in added
        if isinstance(device, devices.VirtualCdrom)
    }
    # Unit 0 boots Windows Setup; unit 1 supplies VMware Tools at first logon.
    assert cdroms == {0: "[ds1] iso/windows-server-2025.iso", 1: vsphere.HOST_TOOLS_ISO_PATH}
    assert vsphere.HOST_TOOLS_ISO_PATH == "[] /vmimages/tools-isoimages/windows.iso"
    assert [option.value for option in config.extraConfig] == ["job-1"]

    boot = created.reconfigured[0].bootOptions
    assert [type(entry) for entry in boot.bootOrder] == [
        real_vim.vm.BootOptions.BootableCdromDevice,
        real_vim.vm.BootOptions.BootableDiskDevice,
    ]
    assert boot.bootOrder[1].deviceKey == 2000
    assert boot.bootRetryEnabled is True


async def test_answer_media_is_uploaded_and_attached_as_a_third_cd(monkeypatch) -> None:
    sata = devices.VirtualAHCIController(key=15000)
    install = devices.VirtualCdrom(key=16000, controllerKey=15000, unitNumber=0)
    tools = devices.VirtualCdrom(key=16001, controllerKey=15000, unitNumber=1)
    vm = fake_vm([sata, install, tools])
    folder = SimpleNamespace(name="Datacenters")
    parent_folder = SimpleNamespace(name="Corp", parent=folder)
    datacenter = FakeDatacenter(name="DC 01", parent=parent_folder)
    made: list = []
    content = SimpleNamespace(
        rootFolder=folder,
        fileManager=SimpleNamespace(MakeDirectory=lambda **kwargs: made.append(kwargs)),
    )
    service = service_for(monkeypatch, {"vm-501": vm, "datacenter-21": datacenter}, content)
    requests: list = []

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, context=None, timeout=None):
        requests.append(request)
        return Response()

    monkeypatch.setattr(vsphere.urllib.request, "urlopen", urlopen)

    ref = await service.attach_answer_media(
        TARGET, "vm-501", datacenter_id="datacenter-21", datastore_id=None,
        file_name="infraops-job.iso", content=b"ISO",
    )

    assert ref.datastore_path == "[ds1] infraops-unattend/infraops-job.iso"
    assert made[0]["name"] == "[ds1] infraops-unattend"
    request = requests[0]
    assert request.get_method() == "PUT" and request.data == b"ISO"
    assert request.full_url == (
        "https://vc.example.test:443/folder/infraops-unattend/infraops-job.iso"
        "?dcPath=Corp%2FDC+01&dsName=ds1"
    )
    assert request.get_header("Cookie") == 'vmware_soap_session="abc"'
    cdrom = vm.reconfigured[0].deviceChange[0].device
    assert isinstance(cdrom, devices.VirtualCdrom)
    assert (cdrom.controllerKey, cdrom.unitNumber) == (15000, 2)
    assert cdrom.backing.fileName == ref.datastore_path
    assert cdrom.connectable.startConnected is True


async def test_installation_media_is_disconnected_and_the_vm_boots_from_disk(monkeypatch) -> None:
    def iso_drive(key: int, unit: int, path: str):
        return devices.VirtualCdrom(
            key=key, controllerKey=15000, unitNumber=unit,
            backing=devices.VirtualCdrom.IsoBackingInfo(fileName=path),
            connectable=devices.VirtualDevice.ConnectInfo(connected=True, startConnected=True),
        )

    disk = devices.VirtualDisk(key=2000)
    answered: list = []
    question = SimpleNamespace(
        id="q-1",
        choice=SimpleNamespace(
            choiceInfo=[SimpleNamespace(key="0", label="Yes"), SimpleNamespace(key="1", label="No")]
        ),
    )
    vm = fake_vm([
        disk,
        iso_drive(16000, 0, "[ds1] iso/windows-server-2025.iso"),
        iso_drive(16001, 1, vsphere.HOST_TOOLS_ISO_PATH),
        iso_drive(16002, 2, "[ds1] infraops-unattend/infraops-job.iso"),
    ])
    vm.runtime.question = question

    def answer(questionId, answerChoice):
        answered.append((questionId, answerChoice))
        vm.runtime.question = None

    vm.AnswerVM = answer

    def reconfigure(spec):
        vm.reconfigured.append(spec)
        # The guest locked the drive: the task runs until the question is answered.
        return FakeTask(running_until=lambda: bool(answered))

    vm.ReconfigVM_Task = reconfigure
    service = service_for(monkeypatch, {"vm-501": vm})

    detached = await service.detach_installation_media(TARGET, "vm-501")

    assert detached == ["[ds1] iso/windows-server-2025.iso", vsphere.HOST_TOOLS_ISO_PATH]
    assert answered == [("q-1", "0")]  # the lock is overridden, nobody is asked
    spec = vm.reconfigured[0]
    edited = [change.device for change in spec.deviceChange]
    assert {device.key for device in edited} == {16000, 16001}
    for device in edited:
        assert isinstance(device.backing, devices.VirtualCdrom.RemotePassthroughBackingInfo)
        assert device.connectable.connected is False and device.connectable.startConnected is False
    assert [entry.deviceKey for entry in spec.bootOptions.bootOrder] == [2000]


async def test_data_disks_are_hot_added_once_on_free_units(monkeypatch) -> None:
    controller = devices.VirtualLsiLogicSASController(key=1000)
    os_disk = devices.VirtualDisk(key=2000, controllerKey=1000, unitNumber=0)
    vm = fake_vm([controller, os_disk])
    service = service_for(monkeypatch, {"vm-501": vm})
    requested = [DiskSpec(size_gb=size, provisioning=DiskProvisioning.THIN) for size in (200, 50)]

    added = await service.add_data_disks(TARGET, "vm-501", requested)

    assert added == 2
    new_disks = [change.device for change in vm.reconfigured[0].deviceChange]
    assert [(disk.unitNumber, disk.capacityInKB // (1024 * 1024)) for disk in new_disks] == [(1, 200), (2, 50)]
    assert all(disk.controllerKey == 1000 for disk in new_disks)

    # A retry after the disks exist adds nothing.
    vm.config.hardware.device = [controller, os_disk, *new_disks]
    assert await service.add_data_disks(TARGET, "vm-501", requested) == 0
    assert len(vm.reconfigured) == 1


async def test_data_disks_never_use_the_controller_unit(monkeypatch) -> None:
    controller = devices.VirtualLsiLogicSASController(key=1000)
    used = [devices.VirtualDisk(key=2000 + unit, controllerKey=1000, unitNumber=unit) for unit in range(7)]
    vm = fake_vm([controller, *used])
    service = service_for(monkeypatch, {"vm-501": vm})

    await service.add_data_disks(TARGET, "vm-501", [DiskSpec(size_gb=1)] * 7)

    assert vm.reconfigured[0].deviceChange[0].device.unitNumber == 8


async def test_console_screenshot_returns_its_datastore_path(monkeypatch) -> None:
    vm = fake_vm([], CreateScreenshot_Task=lambda: FakeTask("[ds1] APP-501/APP-501-1.png"))
    service = service_for(monkeypatch, {"vm-501": vm})

    assert await service.capture_screenshot(TARGET, "vm-501") == "[ds1] APP-501/APP-501-1.png"


async def test_iso_header_is_read_with_a_range_request(monkeypatch) -> None:
    datacenter = FakeDatacenter(name="DC01", parent=None)
    service = service_for(monkeypatch, {"datacenter-21": datacenter}, SimpleNamespace(rootFolder=None))
    requests: list = []

    class Response:
        status = 206

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b"x" * size

    def urlopen(request, context=None, timeout=None):
        requests.append(request)
        return Response()

    monkeypatch.setattr(vsphere.urllib.request, "urlopen", urlopen)

    data = await service.read_datastore_file(
        TARGET, "datacenter-21", "[ds1] iso/windows server.iso", max_bytes=34816
    )

    assert len(data) == 34816
    assert requests[0].get_header("Range") == "bytes=0-34815"
    assert requests[0].full_url == (
        "https://vc.example.test:443/folder/iso/windows%20server.iso?dcPath=DC01&dsName=ds1"
    )
