# VM Provisioning Workflow

The React wizard submits a validated request to FastAPI. A background worker persists and
executes every stage; the job page receives live updates over SSE.

## Wizard

1. Choose blank hardware or a Content Library OVF/OVA package.
2. Select vCenter, datacenter, compute placement, host/resource pool, and storage.
3. For blank hardware, optionally select a datacenter-scoped Windows ISO. No ISO is a valid
   infrastructure-only request. For packages, select an OVF/OVA Content Library item.
4. Configure CPU, RAM, firmware, Secure Boot, disks, and the vNIC port group.
5. Guest credentials, guest IP, identity, domain, certificates, and applications are shown
   only as eligible automation when a guest installation/prepared package is expected.
6. Run non-mutating preflight and submit with an idempotency key. The server repeats the
   full preflight on submission and rejects blocked requests, so the dry run is advisory.

The vNIC port group and the IP settings inside a guest are separate operations. A blank VM
without media accepts only the vNIC choice; the payload cannot request a static guest IP.

## Blank VM branches

Without an ISO, InfraOps creates VM hardware, disks, and the virtual network attachment,
leaves the VM powered off, and pauses in `ACTION_REQUIRED`. The durable lifecycle is:

```text
Infrastructure: READY
Guest OS: INSTALLATION_REQUIRED
VMware Tools: NOT_APPLICABLE_YET
Guest provisioning: WAITING_FOR_OS
```

No Tools, guest IP, hostname, domain, script, certificate, or application operation runs.
After an administrator installs and boots an OS, the waiting OS stage can be explicitly
confirmed and resumed. Successful VM creation remains complete, so retry cannot create a
second VM.

With a Windows ISO, InfraOps mounts the selected installer on the first CD drive and the
ESXi host's VMware Tools ISO on a second one, places `Autounattend.xml` on a temporary
virtual floppy, sets CD-first boot order and powers on the VM. For about 20 seconds it
presses the space bar through vSphere (`PutUsbScanCodes`) to answer the installation
media's "Press any key to boot from CD or DVD" prompt. Windows Setup installs the OS,
logs on once automatically and installs VMware Tools from the second drive. The Tools
heartbeat, a sign-in through VMware Tools and the Setup state in the registry prove the OS
is ready, with no confirmation; power state is never accepted as proof. Only then does
guest provisioning continue. The temporary answer media is deleted after
readiness — or as soon as the job fails, is cancelled or is interrupted — and cached copies
of the answer file are removed from the guest. Its plaintext Setup password is never
persisted in InfraOps.

## OVF/OVA package branch

Current “template” scope is specifically a Content Library OVF/OVA package. Classic
inventory VM templates and Content Library VM templates are not returned or accepted.
InfraOps validates the package and destination, calls the OVF filter/deploy REST operations,
then reconciles hardware and vNIC placement.

The package keeps its own firmware and Secure Boot setting. Templates should be published
without running Sysprep, with VMware Tools installed and the local Administrator password
set to the provisioning credential: after the first boot InfraOps signs in through VMware
Tools and runs Sysprep itself with an explicit answer file (computer name, time zone,
locale, keyboard, administrator password, every OOBE page skipped), so each deployment gets
its own identity with no console interaction. For a sealed (already sysprepped) package
InfraOps also attaches the answer file as first-boot media and, when the provisioning
account still signs in at OOBE, restarts Setup with it; see
[vm-deployment-lifecycle.md](vm-deployment-lifecycle.md#windows-first-boot).

The successful deploy result proves only that a vCenter resource was created. InfraOps
powers it on, waits for an existing Tools/open-vm-tools heartbeat and Guest Operations
readiness, then waits until Windows itself reports Setup and OOBE finished with the
requested computer name before any guest configuration. It does not reinstall Tools or silently upgrade an outdated installation.
Missing/not-running Tools pauses for operator action; outdated but running Tools continues
with a warning. A reported non-Windows guest is stopped before any Windows PowerShell guest
action.

## State, errors, and retry

Job state includes `ACTION_REQUIRED` and `INTERRUPTED` (worker died; retryable); step state
includes `WARNING`, `WAITING_FOR_PREREQUISITE`, and `NOT_APPLICABLE`. The UI separately displays infrastructure,
guest OS, VMware Tools, and guest-provisioning state. A missing prerequisite is not a red
failure. Real failures retain human reason/action text and administrator-only diagnostics.

Every pyVmomi `CreateVM_Task`, `ReconfigVM_Task`, and `PowerOnVM_Task` is awaited before its
stage advances. Content Library deploy checks the structured `succeeded` result and created
resource ID. Retry retains succeeded stages. Before any create operation the clone stage
looks the name up: it resumes only on a VM carrying this job's ownership marker and fails
with "name taken" for any other VM, which it never modifies. VM deletion is never an
automatic rollback.

The final-validation stage verifies the requested guest/network/identity state and creates
a grouped checklist artifact. `COMPLETED` means requested provisioning and verification
finished; it is never used for an empty-disk VM just because `CreateVM_Task` succeeded.

## Notifications

The engineer who submitted a job receives an in-app notification when it completes (only
after final validation passed, with the verified FQDN/IP), fails, is interrupted, or needs
attention. Notifications are stored per user (`/api/v1/notifications`; each user sees only
their own) and appear in the header bell, as a pop-up in any open InfraOps tab (polled every
20 seconds, shown once per browser) and as an unread count in the tab title. Browsers
allow desktop pop-ups only on HTTPS pages; over plain HTTP the in-app pop-up and bell
still work.

See [vm-deployment-lifecycle.md](vm-deployment-lifecycle.md) for the audit and state diagrams,
and [vmware-integration.md](vmware-integration.md) for adapter details and privileges.
