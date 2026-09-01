# VM Provisioning Workflow

The wizard guides an operator through nine visible steps; execution continues as an
audited background job.

## Steps

1. **Source** — choose a blank virtual machine or an OVF/OVA Content Library deployment.
2. **Infrastructure** — vCenter, datacenter, cluster and dependent host placement.
   Templates are selected here after the datacenter is known. Changing an upstream target
   clears all stale downstream selections.
3. **Compute** — name, description, vCPU, RAM, virtual disks, firmware and Secure Boot.
4. **Storage** — automatic selection or a manual datastore selected from the target cluster.
5. **Network** — port group and adapter type. Template deployments also support DHCP or
   static IPv4 and an inline conflict check.
6. **Operating System** — choose optional datacenter-scoped ISO installation
   media for a blank VM. OVF/OVA deployments expose supported hostname,
   time-zone and optional domain-join settings.
7. **Certificates** — select administrator-published packages and inspect their public
   certificate metadata before deployment.
8. **Applications** — select from the approved catalog; dependencies resolve automatically.
9. **Review** — inspect source, infrastructure, compute, storage, network, OS, certificates
   and applications, run a non-destructive preflight check, then explicitly create the VM.

Drafts persist in localStorage. Certificate file contents are never added to the wizard
draft or browser storage.

## Source behavior

Template mode retrieves actual OVF/OVA packages from the selected vCenter's
Content Libraries and sends the opaque library-item identifier through the
provisioning request to the OVF deployment operation. Classic inventory VM
templates are not returned or accepted.

Blank mode sends no template or stale guest-automation state. `guest.iso_id`
is nullable: null means no media, while a selected value must come from the ISO
inventory for the chosen datacenter and be accessible to the target cluster.
The provider creates a new VM with empty virtual disks, mounts the selected ISO
in a connected virtual CD-ROM when present, attaches the requested virtual
network, and leaves the VM powered off. The OS and VMware Tools must be
installed before guest networking, certificates or applications can run.

## Certificate registration

Administrators can retain the existing pasted-PEM method or choose a local public X.509
certificate file. `.crt` and `.cer` uploads may use PEM or binary DER encoding; `.pem` is
also supported. Files may be up to 100 KB. Private-key and PKCS#12 files are rejected. The
authenticated registration API parses the normalized certificate and computes its fingerprint
and validity dates server-side.

## Execution sequence

The creation stage is source-aware: it clones the selected template or creates a blank VM.
Both paths revalidate placement, duplicate names and capacity immediately before mutation.
Template deployments then configure hardware, networking and supported guest automation.
Blank deployments configure virtual hardware/network only and skip guest-dependent stages.

Template guest networking uses PowerShell built from validated values. Certificates are
probed by SHA-256 thumbprint, transferred into the managed guest temp directory, imported
with `certutil`, and re-probed. Application dependencies are installed in topological order.

## Errors, retries and rollback

Every failure produces a human message, reason and recommended action plus technical detail
that is visible only to administrators. Retrying preserves successful stages and reuses an
already-created VM rather than creating it again. A post-creation failure keeps the VM and
marks the job partially completed; deletion is never automatic.

The masking rule applies consistently to job details, the standalone job-step
endpoint, SSE snapshots, structured logs and audit detail text. Infrastructure
discovery failures return the human message, reason and recommended action but
never the stored technical detail.

## Discovery, logs and audit API contracts

All infrastructure discovery endpoints require `infrastructure.read`.
Datacenter scope is mandatory for network and ISO discovery:

* `GET /api/v1/infrastructure/networks?vcenter_id=...&datacenter_id=...`
* `GET /api/v1/infrastructure/isos?vcenter_id=...&datacenter_id=...`
* `GET /api/v1/infrastructure/templates?vcenter_id=...&datacenter_id=...`

`GET /api/v1/logs` requires `jobs.read` and returns `items`, `total`, `page`
and `page_size`. Durable provisioning-step rows are projected into structured
events with timestamp, severity, component, message, resource, datacenter/job
context and expandable details. Filters include severity, component,
`datacenter` (a case-insensitive human-name search), job, text search, and
since/until timestamps. The validated datacenter name is persisted on the job;
the managed-object ID is secondary administrator-only log detail.

`GET /api/v1/audit` supports action, actor, resource type, result,
`datacenter`, job, text search, and since/until filters. It adds a readable
`action_label` and resolves `datacenter_name` from durable job context; raw
`detail_text`, internal IDs and artifacts remain administrator-only.

The final-validation stage creates a grouped checklist in the job artifacts. A template VM
must be powered on with VMware Tools running; a blank VM must exist and remain powered off
for OS installation.
