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
                │ REST /api/v1 (JWT header)  SSE /provisioning/jobs/{id}/events (ticket)
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
│  secrets/     encrypted database provider (Fernet, rotatable key)  │
│  models/ schemas/ core/ db/                                        │
└───────────────┬─────────────────────────┬──────────────────────────┘
                │                         │
        PostgreSQL (source of truth)   Redis (job events, SSE tickets,
                ▲                             login rate limits)
        worker process — claims only as many QUEUED jobs as it has free
        slots (SELECT … FOR UPDATE SKIP LOCKED), heartbeats every running
        job, reaps jobs whose worker died, executes the stage pipeline with
        per-stage timeouts, persists every step and publishes events.
```

Processes (docker-compose): `migrate` (one-shot: Alembic migrations, runtime-role grants,
bootstrap), `backend` (API), `worker`, `frontend` (nginx + SPA), `postgres`, `redis`.

## Request lifecycle (provisioning)

1. **Wizard** collects configuration; each step is validated client-side (Zod) and the
   whole request again server-side (Pydantic `ProvisioningRequest`, extra=forbid).
2. **Dry run** (`POST /provisioning/validate`) runs `PreflightValidator`: vCenter reachability,
   object existence, capacity, name policy/uniqueness, IP syntax + conflict sources,
   certificate/application catalog integrity, dependency resolution, credential-reference
   resolvability, installer-root policy.
3. **Submit** (`POST /provisioning/jobs`, optional `Idempotency-Key` header) runs the same
   preflight **on the server** and rejects the request (422, `blocking_checks`) when any
   blocking check fails — the UI's dry run is a convenience, not the gate. It then creates
   `provisioning_jobs` + immutable `vm_provisioning_requests`. Partial unique indexes allow
   only one active job (QUEUED/RUNNING/INTERRUPTED) per VM name (case-insensitive) and one
   reservation per static IPv4 address; concurrent duplicates get 409. A reused
   Idempotency-Key replays the original job only for the same user and an identical body.
4. **Worker** claims the job, materialises the 24 step rows from the ordered registry and
   executes stages sequentially. A stage timeout or a user cancellation cancels the stage
   coroutine, which cancels the in-flight vCenter task (`CancelTask`) or guest process.
5. Every stage transition is committed to `provisioning_job_steps` and published to Redis;
   the API streams those events to the browser over SSE.
6. Failure semantics: if the `clone_vm` creation stage already succeeded the job becomes
   `PARTIALLY_COMPLETED` (VM retained; failed stages retryable); otherwise `FAILED`.
   Temporary unattended answer media is removed on every failure, cancellation and
   interruption path.
7. Completion runs final validation producing a structured checklist artifact rendered by
   the UI.

## Worker liveness

* Each running job carries `worker_id` and `heartbeat_at`; the worker refreshes the
  heartbeat every `WORKER_HEARTBEAT_INTERVAL_SECONDS` (15 s). The heartbeat also delivers
  cancellation requests to the running stage.
* Every worker runs a reaper. A RUNNING job whose heartbeat is older than
  `WORKER_HEARTBEAT_TIMEOUT_SECONDS` (120 s) — worker crash, OOM kill, SIGKILL — becomes
  `INTERRUPTED`: the running step is marked failed/retryable and the job can be retried
  (completed stages are not repeated) or cancelled. A worker that loses ownership of a job
  stops executing it.
* On SIGTERM the worker stops claiming, waits `WORKER_SHUTDOWN_GRACE_SECONDS` (60 s) for
  running jobs and then interrupts them cleanly. Compose sets `stop_grace_period: 90s`.

## State machine

Stage order lives in `workers/state_machine.py`:

validate_request → connect_vcenter → validate_infrastructure → clone_vm* →
configure_hardware → attach_network_adapter → prepare_unattended_install → power_on →
wait_for_guest_os → wait_for_tools → cleanup_unattended_media → configure_guest_network →
validate_network → configure_hostname → join_domain → reboot_guest → wait_guest_ready →
install_root_certificates → install_intermediate_certificates → validate_certificates →
resolve_dependencies → install_applications → validate_applications → final_validation
(24 stages)

\* only destructive stage; it deploys an OVF/OVA package or creates a blank VM according to
the request source. Every VM it creates is tagged with the job id (`extraConfig`
`infraops.job_id`, plus an `infraops-job-id:` annotation line written atomically at
creation). On retry the stage resumes only on a VM carrying **this** job's marker; any other
VM with the requested name — untagged or owned by another job — fails the stage with "name
taken" and is never modified. Resume-after-retry skips SUCCEEDED/SKIPPED steps. Blank
requests without ISO media pause for an administrator to install the OS.

## Key abstractions

| Interface | Implementations | Notes |
|---|---|---|
| `SecretsProvider` | encrypted database (the only provider) | live revision resolution, never logged |
| `VMwareService` | MockVMwareService, VsphereVMwareService | identical DTOs |
| `GuestOperations` | MockGuestOperations, VMwareToolsGuestOperations | uploaded `.ps1` + shell-free runner, see guest-configuration.md |
| `ConflictCheckProvider` | ICMP, DNS, reverse DNS, vCenter inventory (no IPAM connector yet) | aggregated confidence report; active jobs additionally reserve their static IPv4 in the database |

Production startup rejects mock mode; tests explicitly opt into the in-memory adapters.

## Observability

* Structured JSON logs with `request_id` / `job_id` / `user_id` context vars.
* Prometheus text metrics are per process. The API serves `/metrics` (HTTP requests,
  vCenter errors from discovery/preflight). The worker serves `/metrics` and `/health` on
  `WORKER_METRICS_PORT` (default 9102): job counters, stage failures, job duration
  histogram, installer failures, jobs in flight. Scrape both targets on the private network;
  neither is proxied by nginx.
* API: `/health` (liveness) and `/health/ready` (DB, Redis, secrets checks). Worker:
  `/health` reports a stalled claim loop; compose uses it as the worker healthcheck.

## Database

Tables: users, roles, user_roles, refresh_tokens, vcenters, certificate_packages,
certificates, applications, application_dependencies, provisioning_jobs,
provisioning_job_steps, vm_provisioning_requests, audit_events (append-only: UPDATE/DELETE
row triggers + TRUNCATE statement trigger), platform_settings, secret_references.
Migrations via Alembic (`backend/migrations`), run once per deployment by the `migrate`
service as the schema owner (`MIGRATION_DATABASE_URL`). The API and worker connect as a
least-privilege runtime role (`DATABASE_URL`) provisioned by `python -m app.db.roles`.
CI verifies `alembic upgrade/downgrade/upgrade` and `alembic check` (models == migrations).
