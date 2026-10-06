# VM Deployment Lifecycle

Every VM is created empty and Windows Server is installed unattended from an ISO. The only
manual prerequisite is placing the ISO on a datastore of the target datacenter. Nothing
after submission waits for a person: a job either completes and notifies the requesting
engineer, or stops with an actionable error.

## Architecture map

```text
React wizard (frontend/src/features/vm-provisioning)
  -> FastAPI provisioning API (backend/app/api/v1/provisioning.py)
  -> submission/preflight services (backend/app/services/provisioning)
  -> PostgreSQL job + step state (backend/app/models/jobs.py)
  -> worker engine/pipeline (backend/app/workers)
  -> VMwareService (backend/app/services/vmware/base.py)
  -> pyVmomi Web Services API + vCenter datastore file transfer (/folder)
  -> VMware Tools Guest Operations for Windows post-installation
  -> persisted lifecycle fields + SSE job UI + in-app notification
```

## Persisted state model

Job states: `QUEUED`, `RUNNING`, `COMPLETED`, `PARTIALLY_COMPLETED` (the VM exists but a
later stage failed), `FAILED`, `CANCELLED` and `INTERRUPTED` (the executing worker died;
retry resumes at the interrupted stage, or cancel). Step states: `PENDING`, `RUNNING`,
`SUCCEEDED`, `FAILED`, `SKIPPED`, `WARNING`, `NOT_APPLICABLE`, `CANCELLED`. No state waits
for a person.

* Retry is allowed from `FAILED`, `PARTIALLY_COMPLETED`, `CANCELLED` and `INTERRUPTED`; it
  re-runs failed and cancelled stages and keeps every successful one.
* Cancel is allowed from `QUEUED` and `INTERRUPTED` (immediate — unless the job may still
  hold temporary answer media, in which case it is handed to a worker that removes the media
  and then finalises the cancellation) and from `RUNNING` (the running stage and its vCenter
  task are cancelled within one heartbeat).
* Jobs created by workflows that no longer exist (another VM source, or a blank VM without
  installation media) remain readable — the job page shows the stored request as submitted —
  but cannot be retried. Migration 0009 closed any that were still active.

Four independent job fields are exposed:

```text
infrastructure_status      PENDING | CREATING | READY | FAILED
guest_os_status            UNKNOWN | NOT_PRESENT | INSTALLATION_REQUIRED |
                           INSTALLATION_IN_PROGRESS | READY | ERROR
vmware_tools_status        UNKNOWN | NOT_APPLICABLE_YET | NOT_INSTALLED |
                           INSTALLING | RUNNING | NOT_RUNNING | OUTDATED | ERROR
guest_provisioning_status  PENDING | WAITING_FOR_OS | WAITING_FOR_TOOLS |
                           IN_PROGRESS | COMPLETED | NOT_REQUESTED | FAILED
```

`COMPLETED` means Windows was installed, every requested guest configuration was applied
and final verification passed.

## Flow

```text
submit -> server-side preflight (blocking failures create nothing)
validate request and live inventory (ISO, placement, capacity, network)
  -> create_vm: CreateVM_Task, powered off — LSI Logic SAS + OS disk only,
       Windows ISO on SATA 0:0, host VMware Tools ISO on SATA 0:1, CD-then-disk boot
  -> configure_hardware: CPU and memory
  -> attach_network_adapter: VMXNET3/E1000E on the selected port group
  -> prepare_unattended_install: answer ISO (Autounattend.xml) uploaded and attached as a
       third CD (removed on any failure/cancel/interrupt from here on)
  -> power_on: PowerOnVM_Task, space bar pressed every 3 s for 45 s (PutUsbScanCodes) to
       answer "Press any key to boot from CD or DVD"
  -> wait_for_guest_os ("Install Windows"):
       Windows Setup (windowsPE -> specialize -> oobeSystem) with no page shown
       -> AutoLogon once -> first-logon command installs Tools from SATA 0:1
          (setup64.exe /s /v"/qn REBOOT=R")
       -> the Tools heartbeat proves Setup and OOBE finished (no file transfer needed)
  -> wait_for_tools: Tools RUNNING (OUTDATED continues with a warning)
  -> cleanup_unattended_media: delete the answer CD and file, disconnect the Windows and
       Tools ISOs (boot from disk), then scrub cached answer files and AutoLogon values in
       the guest
  -> add_data_disks: hot-add the data disks to the running VM
  -> initialize_data_disks: online, writable, GPT, one NTFS volume each (Data1, Data2, …)
  -> guest IP / hostname / domain join / reboot / certificates / applications
  -> final_validation (includes "Desktop Experience installed") -> COMPLETED
       -> in-app notification to the requesting engineer
```

Every step after `wait_for_tools` that works inside Windows uploads a PowerShell script
through VMware Tools. Those file transfers go **directly to the ESXi host** that runs the
VM, not through vCenter, so the backend and worker must reach every ESXi host over HTTPS
(TCP 443) and trust its certificate (normally signed by the vCenter VMCA). Preflight checks
this for the hosts the VM can be placed on; a transfer that still fails names the host and
whether the connection or the certificate failed.

Timeouts are generous and configurable in **Settings → Default operation time limits**:
VM creation (30 min), Windows installation (120 min, 30–480), VMware Tools verification,
network configuration and guest operations.

## Windows Setup never waits for input

| Pass | Component | Settings |
|---|---|---|
| windowsPE | International-Core-WinPE | Setup UI language, input/system/UI/user locale |
| windowsPE | Setup | Disk 0 wiped (`WillWipeDisk`) and partitioned — GPT EFI/MSR/Windows for EFI, MBR System (active)/Windows for BIOS, one `ModifyPartition` per partition numbered like the partitions, Windows on `C:`; `InstallFrom` `/IMAGE/INDEX` 2 (Standard) or 4 (Datacenter), both with the Desktop Experience; `InstallTo` disk 0; `UserData` `AcceptEula` and, when configured, `ProductKey` |
| specialize | Shell-Setup | `ComputerName`, `TimeZone` |
| oobeSystem | International-Core | input/system/UI/user locale (the region and keyboard pages) |
| oobeSystem | Shell-Setup | OOBE pages hidden (EULA, OEM registration, online/local account, wireless), `ProtectYourPC=3`, `TimeZone`, `AdministratorPassword` from the credential store (or a new Administrators member plus a random, discarded password for the built-in Administrator), single-use `AutoLogon`, first-logon VMware Tools installation |

* **Deterministic disk.** Only the OS disk exists during Setup, so `DiskID 0` is always the
  OS disk. Data disks are hot-added after Windows is running and are matched by size when
  they are formatted.
* **Storage driver.** The OS disk is on LSI Logic SAS, whose driver is in Windows Setup;
  no driver injection is needed. (PVSCSI would need injected drivers.)
* **Answer media** is a small ISO 9660 + Joliet image with `Autounattend.xml` at its root,
  attached as a CD drive. Windows Setup finds it on removable read-only media under BIOS and
  EFI. It is never offered as installation media in the wizard.
* **Windows Server only.** Client editions add OOBE pages (Microsoft account, privacy) and
  Windows 11 requires a vTPM. Preflight reads the ISO's volume label and blocks anything that
  is not Windows Server media (`SSS_…`).
* **Product keys** are stored as credentials with purpose *Windows product key*
  (GVLKs are public; MAK and retail keys are secrets). Volume-license and evaluation media need
  none.
* **Domain-join credentials never go into the answer media.** `join_domain` is a separate
  guest-operations stage that passes the password as a self-deleting file.

* **Desktop Experience, always.** Microsoft's Windows Server media (2016–2025, evaluation,
  volume and retail) list their editions as 1 Standard Core, 2 Standard (Desktop
  Experience), 3 Datacenter Core, 4 Datacenter (Desktop Experience). The wizard offers only
  2 and 4, and final validation reads `InstallationType` from the registry: anything but
  `Server` fails the job with an explanation (the Desktop Experience cannot be added to
  Server Core later).

Windows Server media carry no VMware Tools, and InfraOps installs Tools only at the first
logon — which happens after Setup and OOBE have finished. A running Tools service with Guest
Operations ready therefore proves the installation finished; the stage needs no guest sign-in
or file transfer.

## When something fails

* A failed stage sets the job to `FAILED` (no VM yet) or `PARTIALLY_COMPLETED` (the VM
  exists) with the human message, the reason and the recommended action, and notifies the
  requesting engineer.
* When `power_on`, `wait_for_guest_os` or `wait_for_tools` fails, InfraOps captures a console
  screenshot (`CreateScreenshot_Task`) and records its datastore path on the failed stage.
  Administrators open it from the stage's technical output on the job page. It shows where
  Setup stopped: an EFI boot list or the "Press any key" prompt (Setup never started), a Setup
  error (edition, product key, media), or a desktop without VMware Tools.
* Answer media holds a plaintext password, so it is removed on success, failure, cancellation
  and interruption. If it was removed before the VM booted from it, the preparation stage is
  reset so a retry regenerates it. The cleanup stage fails (retryably) rather than continue
  while a cached copy may remain in the guest.
* Disconnecting the installation ISOs happens before the in-guest cleanup and is best
  effort: a failure is a `WARNING`, because the VM already boots from its disk.

## VMware references

- https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.vm.GuestInfo.html
- https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.vm.GuestInfo.ToolsStatus.html
- https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.VirtualMachine.html#putUsbScanCodes
- https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.VirtualMachine.html#createScreenshot
- https://learn.microsoft.com/windows-hardware/manufacture/desktop/windows-setup-automation-overview
- https://learn.microsoft.com/windows-hardware/customize/desktop/unattend/
