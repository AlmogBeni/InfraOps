# VMware Integration

## Service interface

All vSphere access goes through `app/services/vmware/base.py::VMwareService`:

```python
test_connection / get_datacenters / get_clusters / get_hosts /
get_resource_pools / get_datastores / get_datastore_clusters /
get_networks / get_isos / read_datastore_file
vm_exists / get_vm_info / get_used_ips / resolve_vm_id /
find_vm_ownership / tag_vm_owner
create_vm / configure_hardware / attach_network /
attach_answer_media / remove_answer_media / power_on / reset / send_keystrokes /
wait_for_tools / detach_installation_media / add_data_disks / capture_screenshot
```

Every VM is created empty and Windows Server is installed unattended from an ISO; there is
no other VM source. Callers pass a `VCenterTarget` (id, host, port, **secret references**,
verify_ssl) — raw credential values are resolved from encrypted backend storage at connect
time and never logged. A credential fingerprint invalidates cached sessions immediately
after rotation. In production, connections with `verify_ssl=false` are rejected when saved
and refused at connect time.

## VM ownership marker

Every VM InfraOps creates carries the id of the job that created it, both set atomically in
the `CreateVM_Task` config spec:

* `extraConfig` key `infraops.job_id`, and
* an `infraops-job-id: <uuid>` line in the VM annotation.

`create_vm` resumes only on a VM carrying the current job's marker. Any other VM with the
requested name — untagged, or tagged by another job — fails the stage ("name taken") and is
never reconfigured, powered on, renamed, re-addressed or joined to the domain. Name lookups
refuse ambiguous names (duplicates in different folders) instead of picking one.

## Task cancellation

Blocking pyvmomi calls run in threads. When the awaiting coroutine is cancelled — stage
timeout, user cancellation or worker shutdown — the thread's task poller calls
`CancelTask()` on the in-flight vCenter task and stops polling. Tasks that vCenter cannot
cancel may still complete; because the VM carries the job's marker, a later retry adopts
it instead of treating it as foreign.

## Development test double (`INFRASTRUCTURE_MODE=mock`)

The in-memory adapter is available only when development/test configuration explicitly
selects it. Production configuration validation rejects mock mode. It exercises:

* VM creation consumes datastore capacity and fails on duplicates/insufficient space,
* networks and ISO images are scoped to their simulated datacenters, and cross-datacenter
  selections are rejected at mutation time,
* ISO volume labels (Windows Server, Windows 11 and Ubuntu media) for the Windows Server
  media preflight check,
* Windows "installs" — VMware Tools reports ~3 s after power-on — only when the answer
  media is attached; installation media can be detached and data disks hot-added,
* guest commands mutate shared state (IPs sync back into the inventory used by conflict
  checks; hot-added disks come back as formatted NTFS volumes), certificate thumbprints
  persist per VM, installers honour a `__FAIL__` injection hook for testing failure paths.

## Production adapter (pyvmomi)

`app/services/vmware/vsphere.py` — **environment dependent**: requires a reachable
vCenter and the `pyvmomi` package (installed via requirements).

### Discovery

* Connection cache with TTL + thread-safe eviction; every pyvmomi call runs in a worker
  thread (`asyncio.to_thread`) so the async API is never blocked.
* ISO discovery uses `HostDatastoreBrowser.SearchDatastoreSubFolders_Task` below the
  selected datacenter's datastore folder and returns opaque, revalidated identifiers rather
  than trusting a client-provided datastore path. InfraOps' own answer media
  (`infraops-unattend/`) is never listed.
* `read_datastore_file` downloads the first bytes of a datastore file over the vCenter
  `/folder` endpoint with an HTTP `Range` request. Preflight reads an ISO's primary volume
  descriptor this way to check that it is Windows Server media (volume label `SSS_…`).
* Network discovery is rooted at the selected datacenter's network folder.

### Creating and installing a VM

* `create_vm` creates the VM powered off with:
  * guest OS `windows9Server64Guest`, the requested CPU, memory and firmware (EFI with
    optional Secure Boot, or BIOS);
  * an **LSI Logic SAS** controller with **only the OS disk** (unit 0). Windows Setup ships
    the LSI SAS driver, so the disk is visible under BIOS and EFI without injected drivers,
    and with a single disk the answer file's `DiskID 0` is always the OS disk;
  * a SATA (AHCI) controller with the Windows ISO on SATA 0:0 and the host's VMware Tools
    ISO (`[] /vmimages/tools-isoimages/windows.iso`) on SATA 0:1;
  * boot order CD, then the OS disk, with boot retry every 10 seconds.
  The ISO's datastore must belong to the selected datacenter and compute target when the
  VM is created.
* `configure_hardware` re-applies CPU and memory; `attach_network` replaces existing
  adapters with VMXNET3/E1000E on the selected distributed or standard port group after
  revalidating the datacenter boundary.
* `attach_answer_media` uploads the generated answer ISO (one `Autounattend.xml` at the
  root, ISO 9660 + Joliet) to `[datastore] infraops-unattend/infraops-<job>.iso` with an
  HTTP `PUT` and attaches it as a third, read-only CD drive. Windows Setup searches removable
  read-only media for `Autounattend.xml` under both BIOS and EFI.
* `power_on`, then `send_keystrokes` presses the space bar (`PutUsbScanCodes`) once a second
  for 60 seconds to answer "Press any key to boot from CD or DVD"; the window covers slow
  firmware and ends long before Setup's first restart, when a key press would boot the ISO
  again. A rerun of that stage resets the VM first.
* `wait_for_tools` polls `guest.toolsRunningStatus`, `guest.toolsVersionStatus2`,
  `guest.guestOperationsReady` and the legacy `guest.toolsStatus`. The Tools heartbeat from
  the new installation — installed at first logon from the Tools ISO — is the first sign that
  Windows is installed; the configured `guestId` never counts as proof.
* `remove_answer_media` detaches the answer CD and deletes its datastore file.
* `detach_installation_media` switches the Windows and Tools CD drives to a disconnected
  client device and sets the boot order to the OS disk only. If the guest has locked a
  drive, vCenter raises a question; InfraOps answers it so the media is released without
  anyone at the console.
* `add_data_disks` hot-adds the requested data disks to the LSI SAS controller once Windows
  is running (never using SCSI unit 7). It is idempotent: a retry only adds disks that are
  still missing.
* `capture_screenshot` runs `CreateScreenshot_Task`; the PNG stays in the VM's folder and
  its datastore path is recorded on the failed stage.

### Errors

Tools states are normalized to `NOT_APPLICABLE_YET`, `NOT_INSTALLED`, `INSTALLING`,
`RUNNING`, `NOT_RUNNING`, `OUTDATED`, `ERROR`, or `UNKNOWN`. `toolsOld` is a warning;
`toolsNotRunning` is not treated as uninstalled. All faults are translated to
`InfraOperationError`; `NoPermission` and `InvalidLogin` produce dedicated actionable
messages.

### Required vCenter privileges (least privilege)

Grant the service account the read-only role plus:

| Area | Privilege (vSphere Client name) | Used for |
| --- | --- | --- |
| Virtual machine › Edit inventory | Create new | `CreateVM_Task` |
| Virtual machine › Change configuration | Add new disk | OS disk at creation, hot-added data disks |
| | Add or remove device | Disk controllers, CD drives, answer CD, network adapter |
| | Modify device settings | Disconnecting the installation CD drives |
| | Change CPU count, Change memory | Hardware stage |
| | Change settings | Firmware, Secure Boot and boot order |
| | Advanced configuration | `infraops.job_id` ownership marker |
| | Set annotation | Ownership marker in the annotation |
| Virtual machine › Interaction | Power on, Reset | Starting Windows Setup |
| | Inject USB HID scan codes | The "Press any key" boot prompt |
| | Configure CD media, Connect devices | Attaching and releasing installation media |
| | Answer question | Releasing a CD drive the guest locked |
| | Create screenshot | Console screenshot when an installation stage fails |
| | Guest operating system management by VIX API | Guest operations |
| Virtual machine › Guest operations | Query, Modify, Execute | Configuration through VMware Tools |
| Datastore | Allocate space | OS and data disks |
| | Browse datastore | ISO discovery |
| | Low level file operations | Reading ISO headers and screenshots, uploading answer media |
| | Remove file | Deleting answer media |
| Network | Assign network | Network adapter |
| Resource | Assign virtual machine to resource pool | Placement |

## Adding another hypervisor/cloud later

Implement `VMwareService`'s interface (or introduce a sibling protocol) plus a factory
branch. Discovery DTOs are plain Pydantic models — the frontend needs no changes beyond
any provider-specific fields you choose to add.
