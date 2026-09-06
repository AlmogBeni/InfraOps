# Security Considerations

InfraOps manages privileged infrastructure; compromise of the platform could compromise
vCenter and Windows servers. The following controls are implemented in Phase 1, followed
by deployment hardening guidance.

## Implemented controls

### Authentication & session
* Local mode issues short-lived JWT access tokens (default 15 min) + long-lived refresh
  JWT delivered only as an **HttpOnly, SameSite=Lax cookie scoped to `/api/v1/auth`**
  (Secure flag when `COOKIE_SECURE=true`).
* Passwords hashed with scrypt (memory-hard, per-password salt, constant-time compare).
* Login rate limiting (sliding window per source IP) and uniform failure messages that do
  not reveal account existence.
* The auth layer is provider-shaped so LDAP/OIDC can replace local verification without
  touching route handlers.

### Authorization (RBAC)
* Server-enforced permission matrix (`auth/permissions.py`): viewer (read), operator
  (+ provisioning/retry/cancel), administrator (+ all administration). Frontend checks
  are cosmetic only.
* Technical error details on job steps are stripped from API responses for
  non-administrators.

### Secrets
* vCenter, Windows local-administrator and domain-join credentials are entered through the
  administrator UI and encrypted at rest with Fernet authenticated encryption. The key is
  purpose-derived from the deployment's `SECRET_KEY`.
* The API never returns username/password values. Jobs and vCenter records contain only
  reference names; the worker resolves the latest encrypted revision for each operation,
  so rotations apply without a restart.
* Blank-Windows provisioning builds a temporary answer ISO containing the password because
  Windows Setup requires it. The bytes are created in memory, never logged or stored in job
  payloads, and the ISO is detached and deleted after VMware Tools becomes ready.
* Defensive redaction (`logging.redact`) strips password/token/key/credential keys from
  anything flowing into logs or audit details.

### Audit trail
* Every significant action recorded (login attempts, provisioning lifecycle, stage
  failures/retries, administrative changes) with user, resource, job id, result, source IP.
* `audit_events` is append-only at the database level (BEFORE UPDATE/DELETE trigger).
* Details payloads are JSON-redacted before insert.

### Input & command safety
* Pydantic schemas with `extra="forbid"`; strict regexes for names, GUIDs, thumbprints,
  registry paths, UNC installer paths; subnet/gateway/broadcast/DNS validation via
  `ipaddress`.
* Guest commands built exclusively from validated structured values; PowerShell values
  single-quote escaped; control characters rejected in installer arguments.
* Application execution fails closed unless every installer path is under an
  administrator-configured repository root.
* Operators cannot supply commands, paths or secret retrieval — catalogs are
  administrator-defined and validated server-side (detection rules normalised, unknown
  keys dropped).

### Transport & headers
* CORS restricted to configured origins; security headers set on every response
  (`X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`); nginx adds the
  same for the SPA.
* SSE endpoint accepts the access token via query parameter (EventSource limitation) —
  scoped to read-only job events; keep transport inside TLS.

## Deployment hardening checklist

1. Internal network / VPN only; TLS termination in front; `COOKIE_SECURE=true`.
2. Strong random `SECRET_KEY`; `ENVIRONMENT=production`.
3. Dedicated InfraOps service accounts (vCenter, domain join) distinct from human
   admin accounts, restricted per `docs/vmware-integration.md`.
4. Rotate managed credentials in the UI. Securely back up `SECRET_KEY`; changing it without
   re-encrypting the credential table makes existing values unreadable.
5. Restrict the software repository share so the platform account can read installers but
   operators cannot write them.
6. Protect PostgreSQL backups (job history + audit trail are business records).
7. Consider WORM/export pipelines for `audit_events` if compliance requires tamper
   evidence beyond the DB trigger.
8. Review `/metrics` exposure (currently unauthenticated on the internal port — restrict
   at the reverse proxy if desired).
9. Keep dependencies pinned; rebuild images regularly for CVE updates.
