# Architecture

## Overview

InfraOps is a modular automation portal. Phase 1 ships one complete module — **VM
Provisioning** — on top of generic job machinery designed for future modules
(decommissioning, snapshots, disk expansion, AD/DNS automation, …).

```
┌────────────────────────────────────────────────────────────────────┐
│ frontend/  React 18 + TypeScript + Vite + Tailwind                 │
│   features/vm-provisioning  wizard (9 steps, zod-validated)        │
│   features/jobs             live timeline via SSE                  │
│   features/admin/*          catalogs, connections, settings        │
└───────────────┬────────────────────────────────────────────────────┘
                │ REST /api/v1 (JWT)      SSE /provisioning/jobs/{id}/events
┌───────────────▼────────────────────────────────────────────────────┐
│ backend/app                                                        │
│  api/         thin routers: auth, discovery, provisioning, admin…  │
│  auth/        local provider + RBAC matrix (permissions.py)        │
│  services/    domain logic, no HTTP concerns                       │
│   vmware/     VMwareService: vsphere(pyvmomi); dev test double     │
│   guest/      GuestOperations: vmware_tools; dev test double       │
│   certificates/ store_logic (pure) + deployer                      │
│   applications/ detection_rules, resolver, installer               │
│   network/    validation (pure) + conflict providers               │
│   provisioning/ preflight validator + submit/retry/cancel service  │
│  workers/     state machine, stages, pipeline, engine, events     │
│  repositories/ DB access (jobs, audit)                             │
│  audit/       action constants + redacting recorder                │
│  secrets/     env | vault providers                                │
│  models/ schemas/ core/ db/                                        │
└───────────────┬─────────────────────────┬──────────────────────────┘
                │                         │
        PostgreSQL (source of truth)   Redis (job event pub/sub)
                ▲
        worker process — claims QUEUED jobs with SELECT … FOR UPDATE
        SKIP LOCKED, executes the stage pipeline with per-stage timeouts,
        persists every step and publishes progress events.
```

## Request lifecycle (provisioning)

1. **Wizard** collects configuration; each step is validated client-side (Zod) and the
   whole request again server-side (Pydantic `ProvisioningRequest`, extra=forbid).
2. **Dry run** (`POST /provisioning/validate`) runs `PreflightValidator`: vCenter reachability,
   object existence, capacity, name policy/uniqueness, IP syntax + conflict sources,
   certificate/application catalog integrity, dependency resolution, credential-reference
   resolvability, installer-root policy. Blocking failures prevent submission.
3. **Submit** (`POST /provisioning/jobs`, Idempotency-Key header) creates
   `provisioning_jobs` + immutable `vm_provisioning_requests`; duplicate active jobs for the
   same VM name are rejected (409).
4. **Worker** claims the job, materialises the 23 step rows from the ordered registry and
   executes stages sequentially with `asyncio.wait_for` timeouts.
5. Every stage transition is committed to `provisioning_job_steps` and published to Redis;
   the API streams those events to the browser over SSE.
6. Failure semantics: if the `clone_vm` creation stage already succeeded the job becomes
   `PARTIALLY_COMPLETED` (VM retained; failed stages retryable); otherwise `FAILED`.
7. Completion runs final validation producing a structured checklist artifact rendered by
   the UI.

## State machine

Stage order lives in `workers/state_machine.py`:

validate_request → connect_vcenter → validate_infrastructure → clone_vm* →
configure_hardware → attach_network_adapter → prepare_unattended_install → power_on →
wait_for_tools → cleanup_unattended_media → configure_guest_network → validate_network → configure_hostname → join_domain →
reboot_guest → wait_guest_ready → install_root_certificates →
install_intermediate_certificates → validate_certificates → resolve_dependencies →
install_applications → validate_applications → final_validation

\* only destructive stage; it clones a template or creates a blank VM according to the
request source. Resume-after-retry skips SUCCEEDED/SKIPPED steps, so retries never recreate
a VM that exists. Only historical blank requests without ISO media skip guest-dependent
stages; new blank requests install and provision Windows end-to-end.

## Key abstractions

| Interface | Implementations | Notes |
|---|---|---|
| `SecretsProvider` | encrypted database | live revision resolution, never logged |
| `VMwareService` | MockVMwareService, VsphereVMwareService | identical DTOs |
| `GuestOperations` | MockGuestOperations, VMwareToolsGuestOperations | structured args only |
| `ConflictCheckProvider` | ICMP, DNS, reverse DNS, vCenter inventory, IPAM hook | aggregated confidence report |

Production startup rejects mock mode; tests explicitly opt into the in-memory adapters.

## Observability

* Structured JSON logs with `request_id` / `job_id` / `user_id` context vars.
* Prometheus text metrics at `/metrics` (jobs, durations histogram, stage/guest/installer
  failure counters).
* `/health` (liveness) and `/health/ready` (DB, Redis, secrets checks).

## Database

Tables: users, roles, user_roles, vcenters, certificate_packages, certificates,
applications, application_dependencies, provisioning_jobs, provisioning_job_steps,
vm_provisioning_requests, audit_events (+ append-only trigger), platform_settings,
secret_references. Migrations via Alembic (`backend/migrations`).
