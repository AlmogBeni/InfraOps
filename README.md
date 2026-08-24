# InfraOps — Internal IT Infrastructure Automation Platform

An internal web platform for IT infrastructure teams. **Phase 1** delivers a complete,
auditable **VM Provisioning** workflow against VMware vSphere: template cloning, hardware
customisation, Windows guest networking, corporate certificate deployment and approved
application installation — with a dry-run validator, live job progress, safe retries and a
tamper-resistant audit trail.

> ⚠️ This is a **privileged infrastructure management tool**. It is designed for
> internal networks only and assumes the operator follows least-privilege practices.

---

## Architecture at a glance

```
frontend (React 18 · TypeScript · Vite · Tailwind)
    │  REST /api/v1  +  Server-Sent Events for live job progress
    ▼
backend (FastAPI · Pydantic v2 · SQLAlchemy 2 async)
    │                    │
    │                     └── Redis pub/sub ──► SSE stream
    ▼
PostgreSQL (jobs, steps, catalogs, immutable audit_events)
    ▲
    └── worker process (asyncio job engine, SKIP LOCKED queue)
          ├── VMwareService      → mock | pyvmomi vSphere adapter
          ├── GuestOperations    → mock | VMware Tools guest API
          ├── CertificateDeployer→ certutil into LocalMachine Root/CA
          └── ApplicationInstaller → msiexec/EXE/PowerShell with detection
```

Key design rules:

* Infrastructure integrations live behind service interfaces (`app/services/*`) — HTTP
  handlers never touch vCenter or guest APIs directly.
* `INFRASTRUCTURE_MODE=mock` provides a fully functional simulated estate so the entire
  workflow runs without any VMware.
* Secrets are resolved by name through a `SecretsProvider` (env / Vault). No credentials
  exist in code, configuration or the database.
* Every provisioning stage is persisted with start/finish times, human-readable output,
  technical error detail and retry state.

Full details: [`docs/architecture.md`](docs/architecture.md)

---

## Prerequisites

* Docker Engine ≥ 24 with the compose plugin (the only hard requirement)
* Node.js ≥ 20 + npm (only for frontend development outside Docker)
* Python ≥ 3.12 (only for backend development outside Docker)

## Quick start (Docker)

```bash
cp .env.example .env        # adjust values; defaults work for mock mode
docker compose up --build
```

This starts:

| Service  | URL / port                | Notes                                   |
|----------|---------------------------|-----------------------------------------|
| frontend | http://localhost:8080     | nginx serving the built SPA + API proxy |
| backend  | http://localhost:8000     | FastAPI, OpenAPI docs at `/api/docs`    |
| worker   | —                         | provisioning job engine                 |
| postgres | internal :5432            | migrations run automatically            |
| redis    | internal :6379            | job event pub/sub                       |

Migrations (`alembic upgrade head`) and seed data run automatically on backend start.

### Development seed accounts

| Username  | Role           | Password                          |
|-----------|----------------|-----------------------------------|
| `admin`   | administrator  | value of `DEV_ADMIN_PASSWORD`     |
| `operator`| operator       | same                              |
| `viewer`  | viewer         | same                              |

Default `.env.example` password: `ChangeMe_DevOnly!123` — **never use in production**.

## Local development without Docker

```bash
# Backend (from repo root)
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export DATABASE_URL="postgresql+asyncpg://infraops:infraops@localhost:5432/infraops"
export REDIS_URL="redis://localhost:6379/0"
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload            # terminal 1
python -m app.workers.runner             # terminal 2

# Frontend (from repo root)
cd frontend
npm install
npm run dev                              # http://localhost:5173 (proxies /api → :8000)
```

## Environment variables

See [`.env.example`](.env.example) for the full annotated list. Highlights:

| Variable | Purpose |
|---|---|
| `INFRASTRUCTURE_MODE` | `mock` (default) or `real` (pyvmomi vSphere adapter) |
| `SECRETS_PROVIDER` | `env` (development) or `vault` (HashiCorp Vault KV v2) |
| `SECRETS_<NAME>` | env-provider secret values, e.g. `SECRETS_VCSA_PROD_PASSWORD` |
| `DATABASE_URL` / `REDIS_URL` | PostgreSQL (asyncpg) and Redis DSNs |
| `SECRET_KEY` | JWT signing key — change for anything beyond local dev |
| `CORS_ORIGINS` | allowed browser origins |

## Database migrations

```bash
cd backend
alembic upgrade head                      # apply
alembic revision --autogenerate -m "..."  # create new migration after model changes
```

The initial migration creates all tables plus a PostgreSQL trigger that makes
`audit_events` append-only at the database level.

## Running tests

```bash
# Inside the containerised environment (recommended):
docker compose exec backend pip install -r requirements-dev.txt
docker compose exec backend pytest

# Frontend unit tests:
cd frontend && npm install && npm test
```

Covered areas include IP/subnet validation, provisioning request schemas, dependency
resolution (cycles, missing/disabled deps), the RBAC matrix, certificate store logic and
command construction safety, pipeline stage registry integrity, and end-to-end flows
against the mock VMware/guest providers.

## Mock infrastructure mode

With `INFRASTRUCTURE_MODE=mock` (the default) the platform simulates:

* Sites `HQ`, datacenters `DC01-Corporate` / `DC02-Lab`
* Clusters `PROD-CLUSTER` (DRS), `EDGE-CLUSTER`, `LAB-CLUSTER`; hosts incl. one in maintenance
* Datastores `PROD-SAN-01/02`, `PROD-VSAN-01`, datastore cluster `PROD-SAN-CLUSTER`
* Networks `VLAN100-PROD`, `VLAN200-MGMT`, `VLAN300-DB`, `VLAN400-LAB`
* Templates *Windows Server 2022/2025 – Corporate Base*
* Existing VMs `APP-PROD-004` / `SQL-TEST-002` (duplicate-name & IP-conflict demos)

Cloned VMs consume datastore capacity, register their IPs, gain VMware Tools after power-on
and accept guest commands exactly like the real adapters — including idempotent certificate
and application detection. Try provisioning `APP-PROD-004` to see duplicate-name blocking.

Switching to `INFRASTRUCTURE_MODE=real` swaps in the pyvmomi adapters behind the identical
interfaces; no application code changes are required. See
[`docs/vmware-integration.md`](docs/vmware-integration.md).

## Secret management design

Integrations call `SecretsService.get_secret(name)`; providers:

* **env** — resolves `SECRETS_<NORMALISED_NAME>` variables (development)
* **vault** — HashiCorp Vault KV v2 via HTTP (`VAULT_ADDR`, `VAULT_TOKEN`, mount `secret`;
  each secret exposes its credential under a `value` key)

The database stores only logical references (`secret_references` table + fields like
`password_secret_ref`). See [`docs/security.md`](docs/security.md).

## Security considerations (summary)

* JWT access tokens (15 min) + HttpOnly refresh cookie; RBAC enforced server-side.
* Rate-limited login; uniform authentication failures.
* Audit log is append-only (DB trigger) with defensive redaction of sensitive keys.
* Guest commands are assembled exclusively from validated structured values; installer
  paths must match approved repository roots; operators can never supply commands.
* Technical error detail is hidden from non-administrators.
* Full list and deployment hardening checklist: [`docs/security.md`](docs/security.md).

## Deployment recommendations

1. Serve only on an internal network / VPN; put TLS termination in front (set
   `COOKIE_SECURE=true`).
2. Use a dedicated vCenter service account restricted to the privileges listed in
   [`docs/vmware-integration.md`](docs/vmware-integration.md).
3. Set a strong `SECRET_KEY`, switch `ENVIRONMENT=production`, configure Vault.
4. Run one worker per host is unnecessary — a single worker handles concurrency via
   `WORKER_CONCURRENCY`; scale out safely thanks to `SKIP LOCKED` claiming.
5. Back up PostgreSQL; it holds the authoritative job history and audit trail.

## Adding new automation modules

The job engine is generic (`job_type` discriminator, ordered stage registries, step
timeline, retries, SSE events). A future module (e.g. VM decommissioning) needs:

1. A stage registry (`workers/state_machine.py` pattern) + handlers (`stages.py` pattern).
2. Request/response Pydantic schemas and an API router.
3. Any new integration behind a service interface with mock + real implementations.

No restructuring of jobs, progress, audit or the frontend job view is required.
