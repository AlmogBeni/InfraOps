# VM Provisioning Workflow

The wizard guides an operator through nine visible steps; execution adds two more phases
(provision + validate) as a background job.

## Steps

1. **Infrastructure** — vCenter connection, site, datacenter (all fetched live).
2. **Compute** — cluster (DRS-aware), automatic or manual host placement with live host
   health (state, maintenance mode, CPU/memory %), resource pool.
3. **Storage** — automatic selection or manual datastore with type/capacity/free/usage;
   datastore clusters listed separately.
4. **VM Hardware** — name (policy + uniqueness validated at dry-run), description, vCPU,
   RAM, up to 8 disks (size, thin/thick), UEFI/BIOS, Secure Boot (UEFI only).
5. **Operating System** — template table (name, OS, modified, description), hostname
   (defaults to VM name), optional time zone, optional domain join (domain, OU, logical
   credential reference).
6. **Network** — port group + adapter type; DHCP or static IPv4 (address, mask *or*
   prefix, gateway, primary/secondary/additional DNS). Inline **IP conflict check**
   aggregates ICMP, forward/reverse DNS and the vCenter inventory with an explicit
   confidence statement.
7. **Certificates** — select administrator-published packages; contents (thumbprints,
   stores, expiry) are shown before selection.
8. **Applications** — approved catalog only; dependencies resolved automatically.
9. **Review & Provision** — full human-readable summary, **Dry Run / Validate** button
   rendering the preflight report, then an explicit **Provision VM** action.

Drafts persist in localStorage; navigating back/forward never loses input.

## Execution sequence (worker)

See `docs/architecture.md` for the ordered stage list. Highlights:

* Clone validates duplicate names and capacity again at execution time.
* Guest networking uses PowerShell built exclusively from validated values
  (`New-NetIPAddress`, `Set-DnsClientServerAddress`); DHCP mode enables DHCP instead.
* Network validation pings the gateway and resolves DNS through the configured resolver.
* Hostname change and domain join schedule a controlled reboot; the pipeline waits for
  guest availability afterwards.
* Certificates: presence probe by SHA-256 thumbprint → upload PEM to managed temp →
  `certutil -addstore -f Root|CA` → re-probe verification. Expired certificates are refused.
* Applications: topological order (dependencies first); detection before install makes
  everything idempotent; msiexec exit 3010 maps to REBOOT_REQUIRED.

## Errors, retries, rollback

Every failure produces the triple **human message / reason / recommended action** plus a
preserved technical detail (admin-only in the UI). Example:

> Network Configuration Failed — The VM was created successfully, but Windows networking
> could not be configured. Reason: the configuration command exited with code 1603.
> Recommended action: Verify VMware Tools status and retry the Network Configuration stage.

* **Retry** resets failed (or cancelled) stages to PENDING and requeues; succeeded stages
  are skipped, so cloning never repeats.
* **Rollback is conservative**: a post-clone failure keeps the VM and marks the job
  PARTIALLY_COMPLETED. Deletion is never automatic.
* **Cancel** stops at the next stage boundary; queued jobs cancel immediately.

## Final result

The final-validation stage builds a grouped checklist (VM / Network / Certificates /
Applications) stored as step artifacts and rendered as the completion card, alongside
duration and assigned IP.
