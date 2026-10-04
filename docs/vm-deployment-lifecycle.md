# VM Deployment Lifecycle Audit

## Architecture map

```text
React wizard (frontend/src/features/vm-provisioning)
  -> FastAPI provisioning API (backend/app/api/v1/provisioning.py)
  -> submission/preflight services (backend/app/services/provisioning)
  -> PostgreSQL job + step state (backend/app/models/jobs.py)
  -> worker engine/pipeline (backend/app/workers)
  -> VMwareService (backend/app/services/vmware/base.py)
  -> pyVmomi Web Services API + Content Library REST API
  -> VMware Tools Guest Operations for Windows-only post-provisioning
  -> persisted lifecycle fields + SSE job UI
```

## Audit findings

The source selector and `ProvisioningRequest.source_type` already distinguished blank and
package deployments. `VsphereVMwareService.create_blank_vm` uses `CreateVM_Task`; the
Content Library client uses OVF filter/deploy REST calls. Every pyVmomi VM task is passed to
`_wait_for_task` (which cancels the task if its caller is cancelled), and the OVF result
checks both `succeeded` and the returned resource ID.
Hardware, vNIC attachment, power, guest commands, certificates, and applications are
separate persisted stages. Successful stages are preserved on retry.

The important defects were:

1. `ProvisioningRequest._validate_source` and the media step rejected new blank VMs without
   an ISO, preventing the valid infrastructure-only lifecycle.
2. `_blank_guest_skip` allowed `stage_prepare_unattended_install` to run for OVF/OVA
   sources. That could attach an answer file containing `WillWipeDisk=true` to a deployed
   appliance. The stage is now strictly blank-with-ISO only.
3. The original linear sequence jumped from power-on directly to `wait_for_tools` and
   repeatedly called `MountToolsInstaller`. VM existence, power, OS readiness, mounted
   Tools media, and a running Tools service were conflated.
4. Job and step enums could express only success/failure/skip. A missing OS therefore had
   no accurate terminal-but-resumable representation.
5. `PowerStateInfo` used only deprecated `guest.toolsStatus`, losing the distinction among
   running state, version state, and Guest Operations readiness.
6. Package deployment was called “template cloning” in parts of the code, although current
   production scope is Content Library OVF/OVA only. Classic VM templates, Content Library
   VM templates, OVF, and OVA are not interchangeable APIs.
7. Guest IP configuration was implemented separately from vNIC attachment, but the UI did
   not make that boundary sufficiently explicit for hardware-only VMs.
8. Content Library item discovery exposes no reliable prepared-OS/Tools guarantee. Windows
   PowerShell guest automation could otherwise be attempted against a non-Windows appliance.

## Persisted state model

Job states: `QUEUED`, `RUNNING`, `COMPLETED`, `PARTIALLY_COMPLETED`, `ACTION_REQUIRED`,
`FAILED`, `CANCELLED` and `INTERRUPTED` (the executing worker died; retry resumes at the
interrupted stage, or cancel). Step states include `WARNING`, `WAITING_FOR_PREREQUISITE`,
and `NOT_APPLICABLE`.

* Retry is allowed from `FAILED`, `PARTIALLY_COMPLETED`, `ACTION_REQUIRED` (confirm the
  prerequisite and resume), `CANCELLED` and `INTERRUPTED`.
* Cancel is allowed from `QUEUED`, `INTERRUPTED` and `ACTION_REQUIRED` (immediate — unless
  the job may still hold temporary answer media, in which case it is handed to a worker
  that removes the media and then finalises the cancellation) and from `RUNNING` (the
  running stage and its vCenter task are cancelled within one heartbeat).

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

`COMPLETED` means requested guest provisioning and final verification completed. It is not
used for an empty-disk VM merely because `CreateVM_Task` succeeded.

## Blank VM flow

```text
validate request and live inventory
  -> CreateVM_Task (hardware/disks/optional installation CD)
  -> ReconfigVM_Task (idempotent hardware reconciliation)
  -> ReconfigVM_Task (vNIC to port group)
  -> no ISO: powered off -> OS INSTALLATION_REQUIRED -> ACTION_REQUIRED
  -> ISO: installation ISO on CD 1, the host's VMware Tools ISO on CD 2
          ([] /vmimages/tools-isoimages/windows.iso), temporary answer-file floppy,
          CD-first boot -> PowerOnVM_Task
          (the floppy is removed on any failure/cancel/interrupt from here on)
          -> space bar pressed for 20 s (PutUsbScanCodes) to answer the media's
             "Press any key to boot from CD or DVD" prompt
          -> OS INSTALLATION_IN_PROGRESS
          -> Windows Setup/OOBE (answer file) -> AutoLogon once
          -> first-logon command installs Tools from CD 2 (setup64.exe /s /v"/qn REBOOT=R")
          -> Tools heartbeat, then sign-in + HKLM\SYSTEM\Setup prove Setup finished
          -> OS READY + Tools RUNNING
          -> remove answer floppy + scrub cached answer files in the guest
          -> guest IP/hostname/domain/certificates/apps
          -> final verification -> COMPLETED
```

With an ISO no step waits for a person: if Tools never reports within the stage timeout the
stage fails with console guidance. Only the no-ISO branch resumes on explicit administrator
confirmation, because nobody installs an OS for it. The OS-readiness stage is retried; the
already successful VM creation step is not. Tools and all guest stages wait behind it.

## OVF/OVA package flow

```text
validate package and destination
  -> Content Library OVF filter/deploy (accept_all_EULA)
  -> reconcile CPU/RAM/disks (firmware and Secure Boot are kept from the package)
  -> attach vNIC to selected port group
  -> VM configured for a Windows guest (config.guestId)?
     -> yes: attach temporary first-boot answer floppy (specialize + oobeSystem only)
     -> no: NOT_APPLICABLE
  -> power on
  -> observe an existing Tools/open-vm-tools heartbeat and Guest Operations readiness
     -> missing/not running: ACTION_REQUIRED; never blind reinstall
  -> reject Windows guest commands for a reported non-Windows guest
  -> sign in through VMware Tools and read Windows Setup state
     -> Setup finished with the requested computer name: ready
     -> Setup finished with another name (template not sealed): upload the answer file and
        run sysprep /generalize /oobe /reboot /unattend:<file>
     -> parked at OOBE without the requested name (sealed template, media not used):
        run the same Sysprep command from OOBE
     -> wait until Setup finishes again with the requested computer name
  -> Tools lifecycle: current continues; outdated continues with a warning
  -> remove the floppy, delete cached and uploaded answer files in the guest
  -> Windows guest networking/identity/domain/certificates/apps
  -> final verification -> COMPLETED -> in-app notification to the requesting engineer
```

### Windows first boot

Every Windows deployment gets its own identity from Sysprep and one answer file:

| Pass | Settings |
|---|---|
| specialize | `ComputerName` (the request's computer name), `TimeZone` |
| oobeSystem | `International-Core` input/system/UI/user locale, OOBE pages hidden (EULA, OEM registration, online/local account, wireless), `ProtectYourPC=3`, `TimeZone`, the provisioning credential as `AdministratorPassword` (or a new local Administrators member) |

The file never contains a windowsPE pass, a disk layout, AutoLogon or logon commands.

**Recommended template: not sealed.** Publish the template without running Sysprep, with
VMware Tools installed and its local `Administrator` password set to the provisioning
credential. After the first boot InfraOps signs in through VMware Tools, uploads the answer
file and runs `sysprep /generalize /oobe /reboot /quiet /unattend:<file>` detached (it
reboots the guest). The answer file is passed explicitly, so nothing depends on Windows
discovering media. This is the same model as vSphere guest customization, without depending
on the vCenter version's support for the guest OS.

**Sealed templates** (exported after `sysprep /generalize /oobe`) boot straight into
specialize and OOBE. Windows Setup is documented to look for `Autounattend.xml` on removable
media at the start of each pass, and InfraOps attaches the floppy for that, but Windows
Server 2025 was observed ignoring it: the floppy was readable at OOBE while specialize and
OOBE never referenced it. If the provisioning account can still sign in at OOBE, InfraOps
waits two minutes and then runs the same Sysprep command from OOBE. If it cannot (sealing
usually leaves no usable administrator password), the stage fails after 12 rejected
sign-ins with instructions to republish the template unsealed.

Readiness is proven inside the guest, because a Tools heartbeat also appears during
specialize/OOBE: the provisioning account signs in, `HKLM\SYSTEM\Setup` shows no Setup or
OOBE in progress, ImageState is `IMAGE_STATE_COMPLETE` and Windows reports the requested
computer name. Sign-ins are spaced out (every 60 s, every 15 s once Tools reports the
requested name, 75 s after a rejection) so failed logons stay below the default lockout
threshold. A Sysprep run that exits without restarting Windows fails the stage with the tail
of `C:\Windows\System32\Sysprep\Panther\setuperr.log` (pending updates, per-user apps, domain
membership or the generalize limit are common causes).

Retail/MAK images that prompt for a product key during OOBE are not answered; use
volume-license (KMS/GVLK) or evaluation media for templates.

The OVF deploy completion and guest provisioning completion are deliberately separate.
Current scope does not include classic inventory VM-template `CloneVM_Task`, Content Library
VM-template deployment, or native vSphere `CustomizationSpec` orchestration.

## VMware references

Broadcom documents `GuestInfo` as information largely collected by VMware Tools and exposes
separate running/version/Guest Operations fields. Its Tools enum distinguishes not installed,
not running, old, and current. The Tools installation procedure explicitly mounts virtual
media and then requires an administrator or in-guest process to run the installer. Content
Library OVF deploy creates a VM or virtual appliance from an OVF library item; its successful
result is the created resource, not guest customization completion.

- https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.vm.GuestInfo.html
- https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.vm.GuestInfo.ToolsStatus.html
- https://knowledge.broadcom.com/external/article/316546/installing-and-upgrading-vmware-tools-in.html
- https://developer.broadcom.com/xapis/vsphere-automation-api/latest/api/vcenter/ovf/library-item/ovfLibraryItemId__action%3Ddeploy/post/
