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
          ├── VMwareService      → pyvmomi vSphere adapter
          ├── GuestOperations    → VMware Tools guest API
          ├── CertificateDeployer→ certutil into LocalMachine Root/CA
          └── ApplicationInstaller → msiexec/EXE/PowerShell with detection
```

Key design rules:

* Infrastructure integrations live behind service interfaces (`app/services/*`) — HTTP
  handlers never touch vCenter or guest APIs directly.
* Production requires `INFRASTRUCTURE_MODE=real`; mock adapters are retained only as
  explicitly selected development test doubles.
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
cp .env.example .env        # fill every required production value
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

Migrations (`alembic upgrade head`) and minimal RBAC/bootstrap initialization run
automatically on backend start. No infrastructure, certificate, application, or demo-user
records are created.

### First administrator

On the first start only, set `BOOTSTRAP_ADMIN_USERNAME` and a unique
`BOOTSTRAP_ADMIN_PASSWORD` of at least 16 characters. Once login succeeds, remove the
password from `.env` and restart. Subsequent starts detect the existing administrator.

## Local development without Docker

```bash
# Backend (from repo root)
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export DATABASE_URL="postgresql+asyncpg://infraops:infraops@localhost:5432/infraops"
export REDIS_URL="redis://localhost:6379/0"
alembic upgrade head
python -m app.bootstrap
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
| `INFRASTRUCTURE_MODE` | Must be `real` in production (pyvmomi vSphere adapter) |
| `SECRETS_PROVIDER` | `env` or HashiCorp Vault KV v2 |
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
command construction safety, pipeline stage registry integrity, and adapter contract flows
against test doubles.

## Production data

The database starts empty except for the three RBAC role definitions and the one-time
administrator. Configure vCenter connections, public certificate packages,
approved applications, credential references, and platform policy through the administrator
screens. The `0002_remove_dev_seed_data` migration removes records created by older
versions of the automatic demo seed. See [`docs/production-deployment.md`](docs/production-deployment.md).

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
  paths must match approved repository roots or execution is blocked; operators can never
  supply commands.
* Technical error detail is hidden from non-administrators.
* Full list and deployment hardening checklist: [`docs/security.md`](docs/security.md).

## Deployment recommendations

1. Serve only on an internal network / VPN; terminate TLS at the host reverse proxy. Compose
   publishes the frontend to `127.0.0.1:8080` by default and does not publish the backend.
2. Use a dedicated vCenter service account restricted to the privileges listed in
   [`docs/vmware-integration.md`](docs/vmware-integration.md).
3. Set a strong `SECRET_KEY`, keep `ENVIRONMENT=production`, and configure an appropriate
   secrets provider.
4. Run one worker per host is unnecessary — a single worker handles concurrency via
   `WORKER_CONCURRENCY`; scale out safely thanks to `SKIP LOCKED` claiming.
5. Back up PostgreSQL; it holds the authoritative job history and audit trail.

## Adding new automation modules

The job engine is generic (`job_type` discriminator, ordered stage registries, step
timeline, retries, SSE events). A future module (e.g. VM decommissioning) needs:

1. A stage registry (`workers/state_machine.py` pattern) + handlers (`stages.py` pattern).
2. Request/response Pydantic schemas and an API router.
3. Any new integration behind a service interface with production adapters and isolated
   test doubles.

No restructuring of jobs, progress, audit or the frontend job view is required.
