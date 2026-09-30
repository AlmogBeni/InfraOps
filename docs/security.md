# Security Considerations

InfraOps manages privileged infrastructure; compromise of the platform could compromise
vCenter and Windows servers. The following controls are implemented in Phase 1, followed
by deployment hardening guidance.

## Implemented controls

### Authentication & session
* Local mode issues short-lived JWT access tokens (default 15 min) + long-lived refresh
  JWT delivered only as an **HttpOnly, SameSite=Lax cookie scoped to `/api/v1/auth`**
  (Secure flag when `COOKIE_SECURE=true`).
* Access tokens are accepted **only** in the `Authorization: Bearer` header — never in a
  query string.
* Refresh tokens are registered server-side (`refresh_tokens`, keyed by JWT `jti`). Every
  refresh rotates the token; presenting an already-rotated token (outside a 10-second
  window for concurrent tabs) is treated as theft: the whole token family from that login
  is revoked and `AUTH_REFRESH_REUSE_DETECTED` is audited. Logout revokes the family.
* Passwords hashed with scrypt (memory-hard, per-password salt, constant-time compare).
  Unknown usernames pay the same scrypt cost, so response timing does not reveal whether an
  account exists; failure messages are uniform.
* Login rate limiting in Redis (shared by all API processes, survives restarts), per
  trusted client IP **and** per username; falls back to an in-process limiter if Redis is
  unreachable.
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
  derived from `CREDENTIAL_ENCRYPTION_KEY` (falls back to `SECRET_KEY` for existing
  deployments), so the JWT signing key and the encryption key rotate independently:
  set the new key, keep the old root in `CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`, run
  `python -m app.secrets.rotate`, then drop the old root.
* The API never returns username/password values. Jobs and vCenter records contain only
  reference names; the worker resolves the latest encrypted revision for each operation,
  so rotations apply without a restart.
* Blank-Windows provisioning builds a temporary answer **floppy** (`Autounattend.xml`)
  containing the local administrator password because Windows Setup requires it. The bytes
  are created in memory and never logged or stored in job payloads. The floppy is detached
  and deleted by the `cleanup_unattended_media` stage **and** on every failure,
  cancellation (including cancelling a paused job) and worker-interruption path
  (`UNATTENDED_MEDIA_REMOVED` is audited). The cleanup stage also deletes Windows Setup's
  cached answer-file copies (`C:\Windows\Panther\…`) and AutoLogon residue in the guest.
* Guest scripts never carry secrets in their text or on a command line. The domain-join
  password is uploaded as a separate, randomly named file that the script reads and
  deletes before its body runs, so it does not appear in process-creation events (4688),
  PowerShell script-block logging (4104) or EDR command-line telemetry. (Offline domain
  join via `djoin /provision` on a management host would keep the credential out of the
  guest entirely; it is not implemented yet.)
* Defensive redaction (`logging.redact`) strips password/token/key/credential keys from
  anything flowing into logs or audit details.

### Audit trail
* Every significant action recorded (login attempts, provisioning lifecycle, stage
  failures/retries, administrative changes) with user, resource, job id, result, source IP.
* `audit_events` is append-only at the database level: BEFORE UPDATE/DELETE row triggers
  and a BEFORE TRUNCATE statement trigger.
* The API and worker connect as a **runtime role that owns no tables** (created by
  `python -m app.db.roles`, run by the `migrate` service). On `audit_events` it may only
  INSERT and SELECT, and it cannot drop the triggers or alter the schema. Migrations run
  as the separate schema owner (`MIGRATION_DATABASE_URL`).
* The recorded `source_ip` is the trusted client address (see *Transport*), not a
  client-supplied header.
* Details payloads are JSON-redacted before insert.

### Input & command safety
* Pydantic schemas with `extra="forbid"`; strict regexes for names, GUIDs, thumbprints and
  registry paths; subnet/gateway/broadcast/DNS validation via `ipaddress`.
* Installer paths (`services/applications/paths.py`) must be absolute UNC
  (`\\server\share\…`) or drive paths with no `.`/`..` segments, quotes, wildcards or
  control characters, and the extension must match the installer type. Approved
  repository roots are validated the same way and matched on a path-segment boundary
  (`\\srv\apps` does not approve `\\srv\apps-evil\…`). Execution fails closed
  unless every installer is inside an approved root.
* **No guest command passes through `cmd.exe`.** PowerShell scripts are uploaded as
  randomly named `.ps1` files and run with `powershell.exe -File`; native programs are
  started by an uploaded runner script via `System.Diagnostics.Process` (no shell), with the
  program path and arguments embedded as single-quoted PowerShell literals in the file.
  Characters such as `& | < > ^ % "` therefore have no special meaning anywhere. Control
  characters are rejected in installer arguments.
* Operators cannot supply commands, paths or secret retrieval — catalogs are
  administrator-defined and validated server-side (detection rules normalised, unknown
  keys dropped).

### Transport & headers
* CORS restricted to configured origins; security headers set on every response
  (`X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`); nginx adds the
  same plus a strict `Content-Security-Policy` for the SPA.
* Client IP: nginx resolves the real client address from `X-Forwarded-For` only when the
  TCP peer is the trusted host reverse proxy (`TRUSTED_PROXY_CIDR`) and then **overwrites**
  the header towards the API. The API trusts the header only from peers in
  `TRUSTED_PROXY_CIDRS` and reads the rightmost untrusted hop. `X-Forwarded-Proto` is
  honoured only from the trusted proxy. Clients therefore cannot bypass login rate limits
  or forge audit source addresses.
* SSE (`EventSource` cannot send headers): the browser POSTs with its bearer token to
  `/provisioning/jobs/{id}/events/ticket` and receives a random ticket valid for **one**
  connection to **that** job's stream for 30 seconds (stored hashed in Redis). Only the SSE
  route accepts tickets. Streams end after the access-token lifetime so access is
  re-checked; the client reconnects with a fresh ticket. nginx access logs omit query
  strings.
* Production refuses vCenter connections with TLS verification disabled (both when saving
  a connection and at connect time); trust the vCenter CA via `VCENTER_CA_FILE`.

## Deployment hardening checklist

1. Internal network / VPN only; TLS termination in front; `COOKIE_SECURE=true`.
2. Strong random `SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY`; `ENVIRONMENT=production`.
   Separate database owner and runtime roles (`MIGRATION_DATABASE_URL` / `DATABASE_URL`).
3. Dedicated InfraOps service accounts (vCenter, domain join) distinct from human
   admin accounts, restricted per `docs/vmware-integration.md`.
4. Rotate managed credentials in the UI. Securely back up the credential encryption key
   (`CREDENTIAL_ENCRYPTION_KEY`, or `SECRET_KEY` if unset); rotate it with
   `python -m app.secrets.rotate` as described above.
5. Restrict the software repository share so the platform account can read installers but
   operators cannot write them.
6. Protect PostgreSQL backups (job history + audit trail are business records).
7. Consider WORM/export pipelines for `audit_events` if compliance requires tamper
   evidence beyond the DB trigger.
8. `/metrics` (API) and the worker metrics port are unauthenticated and are not proxied by
   nginx; keep them on the private network.
9. Keep dependencies pinned; rebuild images regularly for CVE updates.
