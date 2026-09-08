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
6. Run non-mutating preflight and submit with an idempotency key.

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

With a Windows ISO, InfraOps mounts the selected installer plus temporary
`Autounattend.xml` media and powers on the VM. Windows Setup performs the OS installation.
At first logon, Windows runs the Tools installer from vSphere-provided media. A later Tools
heartbeat and Guest Operations readiness prove that the OS and in-guest service are ready.
Only then does guest provisioning continue. The temporary answer media is deleted after
readiness; its plaintext Setup password is never persisted in InfraOps.

## OVF/OVA package branch

Current “template” scope is specifically a Content Library OVF/OVA package. Classic
inventory VM templates and Content Library VM templates are not returned or accepted.
InfraOps validates the package and destination, calls the OVF filter/deploy REST operations,
then reconciles hardware and vNIC placement.

The successful deploy result proves only that a vCenter resource was created. InfraOps
powers it on and waits for an existing Tools/open-vm-tools heartbeat and Guest Operations
readiness. It does not attach blank-VM answer media, reinstall Tools, or silently upgrade an
outdated installation. Missing/not-running Tools pauses for operator action; outdated but
running Tools continues with a warning. A reported non-Windows guest is stopped before any
Windows PowerShell guest action.

## State, errors, and retry

Job state includes `ACTION_REQUIRED`; step state includes `WARNING`,
`WAITING_FOR_PREREQUISITE`, and `NOT_APPLICABLE`. The UI separately displays infrastructure,
guest OS, VMware Tools, and guest-provisioning state. A missing prerequisite is not a red
failure. Real failures retain human reason/action text and administrator-only diagnostics.

Every pyVmomi `CreateVM_Task`, `ReconfigVM_Task`, and `PowerOnVM_Task` is awaited before its
stage advances. Content Library deploy checks the structured `succeeded` result and created
resource ID. Retry retains succeeded stages and resolves an existing VM ID before any create
operation. VM deletion is never an automatic rollback.

The final-validation stage verifies the requested guest/network/identity state and creates
a grouped checklist artifact. `COMPLETED` means requested provisioning and verification
finished; it is never used for an empty-disk VM just because `CreateVM_Task` succeeded.

See [vm-deployment-lifecycle.md](vm-deployment-lifecycle.md) for the audit and state diagrams,
and [vmware-integration.md](vmware-integration.md) for adapter details and privileges.
