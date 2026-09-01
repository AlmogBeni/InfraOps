# VMware Integration

## Service interface

All vSphere access goes through `app/services/vmware/base.py::VMwareService`:

```python
test_connection / get_datacenters / get_clusters / get_hosts /
get_resource_pools / get_datastores / get_datastore_clusters /
get_networks / get_templates
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
* Clone uses `CloneVM_Task` with relocation spec (host/resource pool/datastore), CPU,
  memory, EFI firmware (+ Secure Boot boot options), power-on disabled.
* Hardware stage reconfigures CPU/memory, grows existing disks (never shrinks) and creates
  additional disks with thin/thick backing.
* Network stage replaces existing adapters with VMXNET3/E1000E backed by the selected
  distributed port group or standard network.
* Tools readiness polls `guest.toolsStatus ∈ {toolsOk, toolsOld}`.
* All faults are translated to `InfraOperationError`; `NoPermission` and `InvalidLogin`
  produce dedicated actionable messages.
* Classic VM templates are discovered from the vCenter-wide VM inventory and assigned to
  datacenters by their folder ancestry. Every query logs the selected datacenter, visible VM
  count, visible classic-template count, returned count, and cross-datacenter exclusions.
  A successful zero-result query is logged at warning level instead of being silent.

Content Library VM Templates are a separate vSphere object type and cannot be passed to the
classic `CloneVM_Task` workflow. Convert or clone the library item to a classic inventory VM
template before selecting it in InfraOps.

### Required vCenter privileges (least privilege)

Grant the service account (read-only role plus):

* `VirtualMachine.Provisioning.Clone template` / `Customize` / `Deploy from template`
* `VirtualMachine.Config.*` for: Add existing disk, Add new disk, Raw device, Change CPU,
  Memory, Settings, Add/remove network adapter
* `Network.Assign network`
* `Datastore.Allocate space`, `Browse datastore`
* `Resource.Assign virtual machine to resource pool`

## Adding another hypervisor/cloud later

Implement `VMwareService`'s interface (or introduce a sibling protocol) plus a factory
branch. Discovery DTOs are plain Pydantic models — the frontend needs no changes beyond
any provider-specific fields you choose to add.
