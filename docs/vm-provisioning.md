# VM Provisioning Workflow

The wizard guides an operator through eight visible steps; execution continues as an
audited background job.

## Steps

1. **Source** — choose a blank virtual machine or an OVF/OVA Content Library deployment.
2. **Infrastructure** — vCenter, datacenter, cluster and dependent host placement.
   Templates are selected here after the datacenter is known. Changing an upstream target
   clears all stale downstream selections.
3. **Media** — choose an OVF/OVA package or a mandatory datacenter-scoped Windows ISO.
4. **Configuration** — compute, storage, Windows identity, locale, keyboard layout,
   time zone, certificates, and applications.
5. **Administrator credential** — choose the managed local credential used by Windows
   Setup and later VMware Tools operations.
6. **Network** — port group, adapter, DHCP/static IPv4, and conflict check.
7. **Directory** — optionally choose the AD domain, OU and separate domain-join credential.
8. **Review** — inspect source, infrastructure, compute, storage, network, OS, certificates
   and applications, run a non-destructive preflight check, then explicitly create the VM.

Drafts persist in localStorage. Certificate file contents are never added to the wizard
draft or browser storage.

## Source behavior

Template mode retrieves actual OVF/OVA packages from the selected vCenter's
Content Libraries and sends the opaque library-item identifier through the
provisioning request to the OVF deployment operation. Classic inventory VM
templates are not returned or accepted.

Blank mode requires a Windows ISO from the chosen datacenter that is accessible to the
target cluster. InfraOps creates the VM, mounts the Windows ISO plus a temporary
`Autounattend.xml` ISO, powers it on, completes Setup/OOBE, requests the vSphere-provided
VMware Tools installer, and waits up to two hours for Tools. It then deletes the answer
media and continues with network configuration, optional AD join, certificates and apps.

## Certificate registration

Administrators can retain the existing pasted-PEM method or choose a local public X.509
certificate file. `.crt` and `.cer` uploads may use PEM or binary DER encoding; `.pem` is
also supported. Files may be up to 100 KB. Private-key and PKCS#12 files are rejected. The
authenticated registration API parses the normalized certificate and computes its fingerprint
and validity dates server-side.

## Execution sequence

The creation stage is source-aware: it clones the selected template or creates a blank VM.
Both paths revalidate placement, duplicate names and capacity immediately before mutation.
Both template and blank-Windows deployments then configure hardware, networking and
supported guest automation. Historical blank jobs created without an ISO remain readable
but keep their old powered-off behavior; new requests cannot choose that path.

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

The final-validation stage creates a grouped checklist in the job artifacts. Every new
workflow must finish powered on with VMware Tools running and its requested guest/network
identity verified.
