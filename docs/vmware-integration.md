# VMware Integration

## Service interface

All vSphere access goes through `app/services/vmware/base.py::VMwareService`:

```python
test_connection / get_datacenters / get_clusters / get_hosts /
get_resource_pools / get_datastores / get_datastore_clusters /
get_networks / get_templates / get_isos
vm_exists / get_vm_info / get_used_ips / resolve_vm_id
clone_from_template / create_blank_vm / configure_hardware / attach_network /
power_on / wait_for_tools
```

Callers pass a `VCenterTarget` (id, host, port, **secret references**, verify_ssl) — raw
credentials never travel through the application; they are resolved from the secrets
provider at connect time and never cached or logged.

## Development test double (`INFRASTRUCTURE_MODE=mock`)

The in-memory adapter is available only when development/test configuration explicitly
selects it. Production configuration validation rejects mock mode. It exercises:

* clones consume datastore capacity and fail on duplicates/insufficient space,
* OVF/OVA packages, networks and ISO images are scoped to their simulated
  datacenters, and cross-datacenter selections are rejected at mutation time,
* blank VM creation records the selected ISO as mounted installation media,
* VMware Tools become ready ~3 s after power-on (`wait_for_tools` polls),
* guest commands mutate shared state (IPs sync back into the inventory used by conflict
  checks), certificate thumbprints persist per VM, installers honour a `__FAIL__`
  injection hook for testing failure paths.

## Production adapter (pyvmomi)

`app/services/vmware/vsphere.py` — **environment dependent**: requires a reachable
vCenter and the `pyvmomi` package (installed via requirements).

Implementation notes:

* Connection cache with TTL + thread-safe eviction; every pyvmomi call runs in a worker
  thread (`asyncio.to_thread`) so the async API is never blocked.
* OVF/OVA discovery uses the supported vSphere Content Library REST API. Only
  items whose actual Content Library type is `ovf` are returned. An OVA label is
  used only when the item's file inventory contains an `.ova` file; unavailable
  metadata remains null.
* OVF/OVA deployment uses the vSphere OVF library-item filter/deploy operations
  with the selected resource pool, optional host/datastore, and package network
  mappings. Classic inventory VM templates are deliberately rejected.
* ISO discovery uses `HostDatastoreBrowser.SearchDatastoreSubFolders_Task` below
  the selected datacenter's datastore folder and returns opaque, revalidated
  identifiers rather than trusting a client-provided datastore path.
* Blank VM creation can add a connected virtual CD-ROM backed by the selected
  ISO. The datastore must still belong to the selected datacenter and target
  cluster when the VM mutation runs.
* Hardware stage reconfigures CPU/memory, grows existing disks (never shrinks) and creates
  additional disks with thin/thick backing.
* Network discovery is rooted at the selected datacenter's network folder. The
  network stage revalidates that boundary before replacing existing adapters
  with VMXNET3/E1000E backed by the selected distributed port group or standard
  network.
* Tools readiness polls `guest.toolsStatus ∈ {toolsOk, toolsOld}`.
* All faults are translated to `InfraOperationError`; `NoPermission` and `InvalidLogin`
  produce dedicated actionable messages.
Content Library items are vCenter-scoped rather than owned by an inventory
datacenter. Their `datacenter_id` and `datacenter_name` fields are therefore
null unless vCenter itself supplies a real association; InfraOps does not invent
one. Deployment placement is independently validated against the chosen
datacenter and cluster.

### Required vCenter privileges (least privilege)

Grant the service account (read-only role plus):

* `VirtualMachine.Provisioning.Clone template` / `Customize` / `Deploy from template`
* `VirtualMachine.Config.*` for: Add existing disk, Add new disk, Raw device, Change CPU,
  Memory, Settings, Add/remove network adapter
* `Network.Assign network`
* `Datastore.Allocate space`, `Browse datastore`
* `Resource.Assign virtual machine to resource pool`
* Content Library read and OVF deployment access for each library exposed to
  InfraOps

## Adding another hypervisor/cloud later

Implement `VMwareService`'s interface (or introduce a sibling protocol) plus a factory
branch. Discovery DTOs are plain Pydantic models — the frontend needs no changes beyond
any provider-specific fields you choose to add.
