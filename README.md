# InfraOps — Internal IT Infrastructure Automation Platform

An internal web platform for IT infrastructure teams. **Phase 1** delivers a complete,
auditable **VM Provisioning** workflow against VMware vSphere: OVF/OVA package deployment or
blank-VM creation with unattended Windows installation, hardware
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
    └── worker process (asyncio job engine, SKIP LOCKED queue, heartbeats + reaper)
          ├── VMwareService      → pyvmomi vSphere adapter
          ├── GuestOperations    → VMware Tools guest API (uploaded .ps1, no cmd.exe)
          ├── CertificateDeployer→ certutil into LocalMachine Root/CA
          └── ApplicationInstaller → msiexec/EXE/PowerShell with detection
```

Key design rules:

* Infrastructure integrations live behind service interfaces (`app/services/*`) — HTTP
  handlers never touch vCenter or guest APIs directly.
* Production requires `INFRASTRUCTURE_MODE=real`; mock adapters are retained only as
  explicitly selected development test doubles.
* Administrators enter credential pairs in the UI. Values are encrypted in PostgreSQL
  with a key derived from `CREDENTIAL_ENCRYPTION_KEY` (or `SECRET_KEY` when unset), never
  returned by the API, and resolved live.
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
| frontend | http://127.0.0.1:8080     | nginx serving the built SPA + API proxy |
| backend  | internal :8000            | FastAPI (OpenAPI docs at `/api/docs` only outside production) |
| worker   | internal :9102            | provisioning job engine; `/metrics` + `/health` |
| migrate  | one-shot                  | migrations, runtime DB role grants, bootstrap |
| postgres | internal :5432            | source of truth                         |
| redis    | internal :6379            | job events, SSE tickets, login rate limits |

The one-shot `migrate` service runs `alembic upgrade head` as the schema owner, provisions
the least-privilege runtime database role (`python -m app.db.roles`) and performs minimal
RBAC/bootstrap initialization. The API and worker start only after it succeeds, so scaled
replicas never race on migrations. No infrastructure, certificate, application, or
demo-user records are created.

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
export ENVIRONMENT=development INFRASTRUCTURE_MODE=mock COOKIE_SECURE=false
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export MIGRATION_DATABASE_URL="postgresql+asyncpg://infraops:infraops@localhost:5432/infraops"
export DATABASE_URL="postgresql+asyncpg://infraops_app:app-password@localhost:5432/infraops"
export REDIS_URL="redis://localhost:6379/0"
alembic upgrade head
python -m app.db.roles                   # creates/updates the runtime role from DATABASE_URL
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
| `MIGRATION_DATABASE_URL` | schema-owner DSN, used only by migrations and `app.db.roles` |
| `DATABASE_URL` / `REDIS_URL` | runtime-role PostgreSQL (asyncpg) and Redis DSNs |
| `SECRET_KEY` | JWT signing key |
| `CREDENTIAL_ENCRYPTION_KEY` | stored-credential root key (defaults to `SECRET_KEY`); rotate with `python -m app.secrets.rotate` |
| `TRUSTED_PROXY_CIDR` / `BACKEND_TRUSTED_PROXY_CIDRS` | peers allowed to set `X-Forwarded-For` (nginx / API) |
| `WORKER_*` | concurrency, heartbeat timeout, shutdown grace, metrics port |
| `CORS_ORIGINS` | allowed browser origins |

## Database migrations

```bash
cd backend
alembic upgrade head                      # apply
alembic revision --autogenerate -m "..."  # create new migration after model changes
```

Migrations create all tables plus PostgreSQL triggers that make `audit_events` append-only
(UPDATE/DELETE/TRUNCATE are rejected). Run migrations as the schema owner; the application
connects as a runtime role that owns nothing (see `docs/security.md`). CI checks that the
models and migrations match (`alembic check`) and that the latest migration downgrades
cleanly.

## Running tests

```bash
# Backend (from backend/, with requirements-dev.txt installed)
ruff check app tests migrations
pytest                                   # unit tests
INFRAOPS_TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration
                                         # PostgreSQL guarantees (migrated database)

# Frontend
cd frontend && npm ci && npm run typecheck && npm test
```

GitHub Actions (`.github/workflows/ci.yml`) runs all of the above against PostgreSQL 16 and
Redis 7, verifies migrations, builds both images and validates the rendered nginx
configuration and the compose file.

Covered areas include IP/subnet validation, provisioning request schemas, dependency
resolution (cycles, missing/disabled deps), the RBAC matrix, certificate store logic,
shell-free guest command construction and secret handling, installer path policy, trusted
client-IP resolution, VM ownership and vCenter task cancellation, unattended-media cleanup,
pipeline stage registry integrity, adapter contract flows against test doubles, and — with a
database — unique-reservation races, worker slot claiming/heartbeats, refresh-token reuse
detection and the append-only audit trail.

## Production data

The database starts empty except for the three RBAC role definitions and the one-time
administrator. Configure vCenter connections, public certificate packages,
approved applications, credential references, and platform policy through the administrator
screens. The `0002_remove_dev_seed_data` migration removes records created by older
versions of the automatic demo seed. See [`docs/production-deployment.md`](docs/production-deployment.md).

## Credential management design

Administrators create and rotate vCenter, Windows provisioning-administrator and domain-join
credential pairs under **Administration → Credentials**. The backend encrypts both values
before storing them, returns metadata only, and reads the current revision at operation time.
Provisioning jobs persist only the selected reference name. `.env` contains no vCenter,
local-administrator or domain-join values. See [`docs/security.md`](docs/security.md).

## Security considerations (summary)

* JWT access tokens (15 min, `Authorization` header only) + rotating, server-registered
  refresh tokens in an HttpOnly cookie with reuse detection; RBAC enforced server-side.
* SSE uses single-use stream tickets, never tokens in URLs.
* Rate-limited login (Redis, per trusted client IP and per username); uniform, constant-time
  authentication failures. `X-Forwarded-For` is trusted only from configured proxies.
* Audit log is append-only (DB triggers + least-privilege runtime role) with defensive
  redaction of sensitive keys.
* Guest commands never pass through `cmd.exe` and never carry secrets on a command line;
  installer paths must be inside approved repository roots or execution is blocked;
  operators can never supply commands.
* Technical error detail is hidden from non-administrators.
* Full list and deployment hardening checklist: [`docs/security.md`](docs/security.md).

## Deployment recommendations

1. Serve only on an internal network / VPN; terminate TLS at the host reverse proxy. Compose
   publishes the frontend to `127.0.0.1:8080` by default and does not publish the backend.
2. Use a dedicated vCenter service account restricted to the privileges listed in
   [`docs/vmware-integration.md`](docs/vmware-integration.md).
3. Set strong `SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` values and back up the latter;
   losing it makes stored credentials unreadable. Keep `ENVIRONMENT=production`.
4. A single worker handles concurrency via `WORKER_CONCURRENCY`. Additional workers can be
   added safely: each claims only as many jobs as it has free slots (`SKIP LOCKED`), and a
   job whose worker dies is marked `INTERRUPTED` by the reaper and can be retried.
5. Back up PostgreSQL; it holds the authoritative job history and audit trail.

## Adding new automation modules

The job engine is generic (`job_type` discriminator, ordered stage registries, step
timeline, retries, SSE events). A future module (e.g. VM decommissioning) needs:

1. A stage registry (`workers/state_machine.py` pattern) + handlers (`stages.py` pattern).
2. Request/response Pydantic schemas and an API router.
3. Any new integration behind a service interface with production adapters and isolated
   test doubles.

No restructuring of jobs, progress, audit or the frontend job view is required.
