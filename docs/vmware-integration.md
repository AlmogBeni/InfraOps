# VMware Integration

## Service interface

All vSphere access goes through `app/services/vmware/base.py::VMwareService`:

```python
test_connection / get_datacenters / get_clusters / get_hosts /
get_resource_pools / get_datastores / get_datastore_clusters /
get_networks / get_templates / get_isos
vm_exists / get_vm_info / get_used_ips / resolve_vm_id /
find_vm_ownership / tag_vm_owner
clone_from_template / create_blank_vm / configure_hardware / attach_network /
attach_temporary_floppy / mount_tools_installer / remove_temporary_floppy / power_on / wait_for_tools
```

Callers pass a `VCenterTarget` (id, host, port, **secret references**, verify_ssl) — raw
credential values are resolved from encrypted backend storage at connect time and never
logged. A credential fingerprint invalidates cached sessions immediately after rotation.
In production, connections with `verify_ssl=false` are rejected when saved and refused at
connect time.

## VM ownership marker

Every VM InfraOps creates carries the id of the job that created it:

* `extraConfig` key `infraops.job_id` (set in the `CreateVM_Task` config spec for blank VMs;
  written by a reconfigure immediately after an OVF/OVA deployment), and
* an `infraops-job-id: <uuid>` line in the VM annotation, applied atomically at creation
  for both blank VMs and OVF/OVA deployments.

`clone_vm` resumes only on a VM carrying the current job's marker. Any other VM with the
requested name — untagged, or tagged by another job — fails the stage ("name taken") and is
never reconfigured, powered on, renamed, re-addressed or joined to the domain. Name lookups
refuse ambiguous names (duplicates in different folders) instead of picking one. The
service account therefore also needs *Virtual machine → Change Configuration → Advanced
configuration* (extraConfig) permission.

## Task cancellation

Blocking pyvmomi calls run in threads. When the awaiting coroutine is cancelled — stage
timeout, user cancellation or worker shutdown — the thread's task poller calls
`CancelTask()` on the in-flight vCenter task and stops polling. Tasks that vCenter cannot
cancel may still complete; because the VM carries the job's marker, a later retry adopts
it instead of treating it as foreign. Content Library OVF deployments are synchronous REST
calls without a task handle: the request is aborted, and a VM that still appears is
likewise adopted only by its own job.

## Development test double (`INFRASTRUCTURE_MODE=mock`)

The in-memory adapter is available only when development/test configuration explicitly
selects it. Production configuration validation rejects mock mode. It exercises:

* clones consume datastore capacity and fail on duplicates/insufficient space,
* OVF/OVA packages, networks and ISO images are scoped to their simulated
  datacenters, and cross-datacenter selections are rejected at mutation time,
* blank VM creation records the selected Windows ISO and temporary floppy-backed answer media,
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
* Unattended media is uploaded to `[datastore] infraops-unattend`, attached as a virtual
  floppy so the Windows installer remains the VM's only datastore-backed CD-ROM, then
  detached and deleted when Tools reports ready — or immediately when the job fails, is
  cancelled or is interrupted. The service account therefore also needs datastore file
  create/delete permission.
* Hardware stage reconfigures CPU/memory, grows existing disks (never shrinks) and creates
  additional disks with thin/thick backing.
* Network discovery is rooted at the selected datacenter's network folder. The
  network stage revalidates that boundary before replacing existing adapters
  with VMXNET3/E1000E backed by the selected distributed port group or standard
  network.
* Guest readiness reads `guest.toolsRunningStatus`, `guest.toolsVersionStatus2`,
  `guest.guestOperationsReady`, and the legacy `guest.toolsStatus` compatibility value.
  Configured `guestId` is never used as proof that an OS is installed.
* Blank VMs installed from an ISO get the host's VMware Tools ISO
  (`[] /vmimages/tools-isoimages/windows.iso`) on a second CD drive at creation. The
  `FirstLogonCommands` installer runs `setup64.exe` from it (only that ISO has it at its
  root) and a later heartbeat proves installation. `MountToolsInstaller` remains a fallback
  only if Tools is still missing after Windows reported ready. OVF/OVA deployments never
  mount or upgrade Tools automatically.
* The installation media's "Press any key to boot from CD or DVD" prompt is answered with
  `PutUsbScanCodes` (space bar) for 20 seconds after power-on; a rerun of that stage resets
  the VM first.
* Tools states are normalized to `NOT_APPLICABLE_YET`, `NOT_INSTALLED`, `INSTALLING`,
  `RUNNING`, `NOT_RUNNING`, `OUTDATED`, `ERROR`, or `UNKNOWN`. `toolsOld` is a warning;
  `toolsNotRunning` is not treated as uninstalled.
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
* `Datastore.Allocate space`, `Browse datastore`, and low-level file operations needed to
  create/delete temporary answer media
* `Resource.Assign virtual machine to resource pool`
* `VirtualMachine.Interact.PowerOn`, `Reset` and `PutUsbScanCodes` (Inject USB HID scan
  codes, to start Windows Setup from an ISO)
* `VirtualMachine.Interact.GuestControl` and `VirtualMachine.GuestOperations.*` (Query,
  Modify, Execute) for guest configuration through VMware Tools
* Content Library read and OVF deployment access for each library exposed to
  InfraOps

## Adding another hypervisor/cloud later

Implement `VMwareService`'s interface (or introduce a sibling protocol) plus a factory
branch. Discovery DTOs are plain Pydantic models — the frontend needs no changes beyond
any provider-specific fields you choose to add.
