# VM Provisioning Workflow

The React wizard submits a request to FastAPI. A background worker persists and executes
every stage; the job page receives live updates over SSE. Every VM is created empty and
Windows Server is installed unattended from an ISO — the only manual prerequisite is placing
the ISO on a datastore of the target datacenter.

## Wizard

1. **Location** — vCenter, datacenter, compute target, automatic or manual host placement.
2. **Windows media** — a Windows Server ISO from the selected datacenter (required) and,
   for retail or MAK media, a stored product key (volume-license and evaluation media need
   none).
3. **Configuration** — VM name, CPU, memory, firmware (UEFI with optional Secure Boot, or
   BIOS), the OS disk and up to seven data disks, storage placement, Windows computer name,
   time zone, language, keyboard, edition index, certificate packages and applications.
4. **Administrator** — the encrypted local administrator credential Windows Setup creates
   and InfraOps signs in with.
5. **Network** — port group, adapter, and DHCP or static IPv4 with DNS (with an optional
   address-conflict check).
6. **Domain join** — optional AD domain, OU and domain-join credential.
7. **Review** — the full plan. *Run preflight checks* is available on demand.

**Create virtual machine** submits immediately with an idempotency key; there is no
confirmation dialog and no mandatory dry run. The server runs the complete preflight on
every submission and creates nothing when a blocking check fails; the wizard then lists the
blocking checks inline. Preflight covers the vCenter connection, placement, capacity, the
ISO (present, reachable from the compute target, and Windows Server media), the product
key, credentials, VM name and ownership, static-IP conflicts, certificates, applications and
installer-root policy.

## What happens after submission

InfraOps creates the VM with only its OS disk, attaches the Windows ISO, the host's VMware
Tools ISO and a temporary answer-file CD, powers it on and answers the "Press any key to boot
from CD or DVD" prompt itself. Windows Setup installs and configures Windows without showing
any page, logs on once and installs VMware Tools. A sign-in through VMware Tools and the Setup
state in the registry prove Windows is ready. InfraOps then removes the answer media and its
cached copies (the plaintext Setup password is never persisted in InfraOps), disconnects
the installation ISOs, hot-adds and formats the data disks, configures the network, computer
name and domain, installs certificates and applications and runs the final validation.

See [vm-deployment-lifecycle.md](vm-deployment-lifecycle.md) for every stage and the answer
file, and [vmware-integration.md](vmware-integration.md) for adapter details and vCenter
privileges.

## State, errors, and retry

The UI separately displays infrastructure, guest OS, VMware Tools and guest-provisioning
state. Real failures keep a human message, reason and recommended action;
administrator-only diagnostics include the technical detail and, for installation stages,
a console screenshot taken when the stage failed.

Every pyVmomi task is awaited before its stage advances. Retry re-runs failed stages and
keeps succeeded ones. Before creating anything the `create_vm` stage looks the name up: it
resumes only on a VM carrying this job's ownership marker and fails with "name taken" for any
other VM, which it never modifies. VM deletion is never an automatic rollback.

The final-validation stage verifies the VM, data volumes, network, identity, certificates and
applications and stores a grouped checklist. `COMPLETED` means Windows was installed and every
requested setting was applied and verified.

Jobs created by earlier workflows that no longer exist stay viewable, read-only, with their
stored request; they cannot be retried — submit a new request instead.

## Notifications

The engineer who submitted a job receives an in-app notification when it completes (only
after final validation passed, with the verified FQDN/IP), fails or is interrupted.
Notifications are stored per user (`/api/v1/notifications`; each user sees only their own)
and appear in the header bell, as a pop-up in any open InfraOps tab (polled every 20 seconds,
shown once per browser) and as an unread count in the tab title. Browsers allow desktop
pop-ups only on HTTPS pages; over plain HTTP the in-app pop-up and bell still work.
