"""Focused tests for datacenter-scoped vSphere compute discovery."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import NotFoundError
from app.services.vmware import vsphere as vsphere_module
from app.services.vmware.base import CloneSpec, VCenterTarget, VmRef
from app.services.vmware.vsphere import VsphereVMwareService


class FakeView:
    def __init__(self, entries: list[object]) -> None:
        self.view = entries
        self.destroyed = False

    def Destroy(self) -> None:
        self.destroyed = True


class FakeViewManager:
    def __init__(self, entries_by_container: dict[int, list[object]]) -> None:
        self.entries_by_container = entries_by_container
        self.calls: list[tuple[object, list[type], bool]] = []
        self.views: list[FakeView] = []

    def CreateContainerView(
        self,
        container: object,
        entity_types: list[type],
        recursive: bool,
    ) -> FakeView:
        self.calls.append((container, entity_types, recursive))
        view = FakeView(self.entries_by_container.get(id(container), []))
        self.views.append(view)
        return view


class FakeDatacenter:
    def __init__(self, moref: str, host_folder: object) -> None:
        self._moId = moref
        self.hostFolder = host_folder


class FakeComputeResource:
    def __init__(
        self,
        moref: str,
        name: str,
        *,
        drs_enabled: bool | None,
        cpu_cores: int,
        memory_gb: int,
    ) -> None:
        self._moId = moref
        self.name = name
        self.configurationEx = (
            SimpleNamespace(drsConfig=SimpleNamespace(enabled=drs_enabled))
            if drs_enabled is not None
            else SimpleNamespace()
        )
        self.resourcePool = SimpleNamespace(
            _moId=f"resgroup-{moref}",
            name="Resources",
            resourcePool=[],
        )
        self.host = [
            SimpleNamespace(
                hardware=SimpleNamespace(
                    cpuInfo=SimpleNamespace(
                        numCpuCores=cpu_cores,
                        hz=2_500_000_000,
                    ),
                    memorySize=memory_gb * 1024**3,
                ),
                runtime=SimpleNamespace(
                    connectionState=vsphere_module.vim.HostSystem.ConnectionState.connected,
                    inMaintenanceMode=False,
                ),
                summary=SimpleNamespace(
                    quickStats=SimpleNamespace(
                        overallCpuUsage=0,
                        overallMemoryUsage=0,
                    )
                ),
                datastore=[],
                _moId=f"host-{moref}",
                name=f"host-{name}",
            )
        ]


class FakeCluster(FakeComputeResource):
    pass


class FakeStandaloneComputeResource(FakeComputeResource):
    pass


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


@pytest.mark.asyncio
async def test_clusters_are_recursively_discovered_and_scoped_to_each_datacenter(
    target: VCenterTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vsphere_module.vim, "Datacenter", FakeDatacenter)
    monkeypatch.setattr(vsphere_module.vim, "ComputeResource", FakeComputeResource)
    monkeypatch.setattr(vsphere_module.vim, "ClusterComputeResource", FakeCluster)

    production_cluster = FakeCluster(
        "domain-c101",
        "Production Cluster",
        drs_enabled=True,
        cpu_cores=48,
        memory_gb=512,
    )
    lab_compute = FakeStandaloneComputeResource(
        "domain-s202",
        "lab-esx-01.example.test",
        drs_enabled=None,
        cpu_cores=16,
        memory_gb=128,
    )

    # Real vSphere inventories may place clusters below one or more ordinary
    # folders. Directly inspecting hostFolder.childEntity sees only the folder.
    production_host_folder = SimpleNamespace(
        childEntity=[SimpleNamespace(childEntity=[production_cluster])]
    )
    lab_host_folder = SimpleNamespace(
        childEntity=[SimpleNamespace(childEntity=[lab_compute])]
    )
    empty_host_folder = SimpleNamespace(childEntity=[])
    datacenters = {
        "datacenter-101": FakeDatacenter("datacenter-101", production_host_folder),
        "datacenter-202": FakeDatacenter("datacenter-202", lab_host_folder),
        "datacenter-303": FakeDatacenter("datacenter-303", empty_host_folder),
    }

    view_manager = FakeViewManager(
        {
            id(production_host_folder): [production_cluster],
            id(lab_host_folder): [lab_compute],
            id(empty_host_folder): [],
        }
    )
    content = SimpleNamespace(viewManager=view_manager)
    service_instance = SimpleNamespace(RetrieveContent=lambda: content)
    service = object.__new__(VsphereVMwareService)
    references = {
        **datacenters,
        production_cluster._moId: production_cluster,
        lab_compute._moId: lab_compute,
    }
    service._find_by_moref = lambda _content, moref: references.get(moref)

    async def with_session(_target, callback, **_kwargs):
        return callback(service_instance)

    service._with_session = with_session

    production = await service.get_clusters(target, "datacenter-101")
    lab = await service.get_clusters(target, "datacenter-202")
    empty = await service.get_clusters(target, "datacenter-303")

    assert [cluster.id for cluster in production] == ["domain-c101"]
    assert production[0].total_cpu_cores == 48
    assert production[0].total_memory_gb == 512
    assert [cluster.id for cluster in lab] == ["domain-s202"]
    assert lab[0].drs_enabled is False
    assert lab[0].hosts_count == 1
    assert empty == []
    assert [call[0] for call in view_manager.calls] == [
        production_host_folder,
        lab_host_folder,
        empty_host_folder,
    ]
    assert all(
        call[1] == [FakeComputeResource, FakeCluster] and call[2] is True
        for call in view_manager.calls
    )
    assert all(view.destroyed for view in view_manager.views)

    standalone_hosts = await service.get_hosts(target, lab_compute._moId)
    standalone_pools = await service.get_resource_pools(target, lab_compute._moId)
    standalone_datastores = await service.get_datastores(target, lab_compute._moId)
    assert [host.id for host in standalone_hosts] == ["host-domain-s202"]
    assert standalone_hosts[0].available_for_provisioning is True
    assert [pool.id for pool in standalone_pools] == ["resgroup-domain-s202"]
    assert standalone_datastores == []


@pytest.mark.asyncio
async def test_cluster_discovery_rejects_a_non_datacenter_reference(
    target: VCenterTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vsphere_module.vim, "Datacenter", FakeDatacenter)
    monkeypatch.setattr(vsphere_module.vim, "ComputeResource", FakeComputeResource)

    service = object.__new__(VsphereVMwareService)
    service._find_by_moref = lambda _content, _moref: SimpleNamespace(
        _moId="group-not-a-datacenter"
    )
    service_instance = SimpleNamespace(
        RetrieveContent=lambda: SimpleNamespace(viewManager=FakeViewManager({}))
    )

    async def with_session(_target, operation, **_kwargs):
        return operation(service_instance)

    service._with_session = with_session

    with pytest.raises(NotFoundError, match="does not exist"):
        await service.get_clusters(target, "group-not-a-datacenter")


@pytest.mark.asyncio
async def test_ovf_placement_accepts_a_scoped_standalone_compute_resource(
    target: VCenterTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vsphere_module.vim, "Datacenter", FakeDatacenter)
    monkeypatch.setattr(vsphere_module.vim, "ComputeResource", FakeComputeResource)
    monkeypatch.setattr(vsphere_module.vim, "ClusterComputeResource", FakeCluster)

    standalone = FakeStandaloneComputeResource(
        "domain-s404",
        "edge-esx-01.example.test",
        drs_enabled=None,
        cpu_cores=24,
        memory_gb=256,
    )
    host_folder = SimpleNamespace(childEntity=[standalone])
    datacenter = FakeDatacenter("datacenter-404", host_folder)
    view_manager = FakeViewManager({id(host_folder): [standalone]})
    content = SimpleNamespace(viewManager=view_manager)
    service_instance = SimpleNamespace(RetrieveContent=lambda: content)

    class FakeContentLibrary:
        @staticmethod
        def is_library_item(value: str) -> bool:
            return value.startswith("library-item:")

        async def deploy_ovf_package(
            self,
            _target,
            spec,
            *,
            resource_pool_id: str,
        ) -> VmRef:
            assert spec.cluster_id == standalone._moId
            assert resource_pool_id == standalone.resourcePool._moId
            return VmRef(id="vm-404", name=spec.vm_name)

    references = {
        datacenter._moId: datacenter,
        standalone._moId: standalone,
    }
    service = object.__new__(VsphereVMwareService)
    service._content_library = FakeContentLibrary()
    service._find_by_moref = lambda _content, moref: references.get(moref)
    service._find_vm_by_name = lambda _content, _name: None

    async def with_session(_target, callback, **_kwargs):
        return callback(service_instance)

    service._with_session = with_session

    result = await service.clone_from_template(
        target,
        CloneSpec(
            template_id="library-item:edge-appliance",
            vm_name="EDGE-VM-001",
            datacenter_id=datacenter._moId,
            cluster_id=standalone._moId,
        ),
    )

    assert result == VmRef(id="vm-404", name="EDGE-VM-001")
    assert all(view.destroyed for view in view_manager.views)
