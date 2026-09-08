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
`_wait_for_task`, and the OVF result checks both `succeeded` and the returned resource ID.
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

Overall job states include `ACTION_REQUIRED`. Step states include `WARNING`,
`WAITING_FOR_PREREQUISITE`, and `NOT_APPLICABLE`. Four independent job fields are exposed:

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
  -> ISO: attach temporary answer media -> PowerOnVM_Task
          -> OS INSTALLATION_IN_PROGRESS
          -> Windows Setup/OOBE
          -> first-logon in-guest Tools installation
          -> OS READY + Tools RUNNING
          -> guest IP/hostname/domain/certificates/apps
          -> final verification -> COMPLETED
```

No-ISO resume is explicit administrator confirmation. The OS-readiness stage is retried;
the already successful VM creation step is not. Tools and all guest stages wait behind it.

## OVF/OVA package flow

```text
validate package and destination
  -> Content Library OVF filter/deploy
  -> reconcile CPU/RAM/disks
  -> attach vNIC to selected port group
  -> no blank-VM answer media (NOT_APPLICABLE)
  -> power on
  -> observe an existing Tools/open-vm-tools heartbeat and Guest Operations readiness
     -> current: continue
     -> outdated/running: warning, continue without upgrade
     -> missing/not running: ACTION_REQUIRED; never blind reinstall
  -> reject Windows guest commands for a reported non-Windows guest
  -> Windows guest networking/identity/domain/certificates/apps
  -> final verification -> COMPLETED
```

The OVF deploy completion and guest provisioning completion are deliberately separate.
Current scope does not include classic inventory VM-template `CloneVM_Task`, Content Library
VM-template deployment, or native vSphere `CustomizationSpec`/Sysprep orchestration.

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
