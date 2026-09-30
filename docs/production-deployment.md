# Production deployment

InfraOps now fails closed when production security settings are missing. The runtime creates
no sample infrastructure, certificates, applications, credentials, or demo accounts.

## First start

1. Back up PostgreSQL if upgrading an existing deployment. Migration
   `0002_remove_dev_seed_data` deletes only the former seed records identified by
   their exact IDs, email addresses, descriptions, or installer paths. Migration
   `0003_remove_sites` safely drops the obsolete `sites` table when present and also
   succeeds on deployments where that table was never created.
2. Copy `.env.example` to `.env`, fill the required values, and run `chmod 600 .env`.
   Use different strong passwords for the schema owner (`POSTGRES_PASSWORD` /
   `MIGRATION_DATABASE_URL`) and the runtime role (`DATABASE_URL`, user `infraops_app`).
3. Set a one-time bootstrap administrator username and password. Run
   `docker compose up --build`. The one-shot `migrate` service applies migrations, creates
   the runtime role with least-privilege grants and bootstraps the administrator; the API
   and worker start only after it succeeds. Confirm that the administrator can log in.
4. Remove `BOOTSTRAP_ADMIN_PASSWORD` from `.env`, restart, and retain the password in your
   normal enterprise password manager.
5. In **Administration → Credentials**, add encrypted pairs for vCenter, Windows
   provisioning administrator, and (when used) domain join. Then add and test the vCenter
   connection before creating any provisioning job.

The frontend listens on `127.0.0.1:8080` by default for a host-level TLS reverse proxy. The
backend is available only on the private Compose network. Forward the original `Host`,
`X-Forwarded-For`, and `X-Forwarded-Proto` headers and preserve long-lived, unbuffered SSE
connections under `/api/v1/provisioning/jobs/*/events`.

Client addresses: the bundled nginx accepts `X-Forwarded-For`/`X-Forwarded-Proto` only from
`TRUSTED_PROXY_CIDR` — the address the host reverse proxy has as seen from the frontend
container (normally the Docker bridge gateway, inside the default `172.16.0.0/12`). Narrow
it to that exact address when known (`docker network inspect infraops_default`). nginx then
overwrites the header towards the API, which trusts it only from the private Compose
network (`BACKEND_TRUSTED_PROXY_CIDRS`). Direct clients can therefore not spoof their
address to evade login rate limits or falsify audit records.

## Upgrading to the hardening release (migration 0007)

1. Back up PostgreSQL.
2. Add `MIGRATION_DATABASE_URL` with the existing owner credentials (`POSTGRES_USER`) and
   change `DATABASE_URL` to a new runtime role, e.g. `infraops_app`, with its own password.
   `migrate` creates the role and grants. Keeping a single role still works but logs a
   warning and forgoes the tamper-resistance of the audit trail.
3. Optionally set `CREDENTIAL_ENCRYPTION_KEY`; existing ciphertext stays readable through
   the `SECRET_KEY` fallback. Run `docker compose run --rm backend python -m
   app.secrets.rotate` to re-encrypt under the new key.
4. The migration closes duplicate active jobs for the same VM name (keeps the oldest) and
   backfills static-IP reservations. Jobs left `RUNNING` by the previous worker become
   `INTERRUPTED` once their heartbeat is stale and can be retried or cancelled.
5. Refresh tokens issued before the upgrade are not registered; users sign in again once.
6. In production, vCenter connections with TLS verification disabled are refused; enable
   verification and provide the CA via `VCENTER_CA_FILE` before upgrading.
7. VMs created before the upgrade carry no ownership marker. If an old job whose VM-creation
   stage did not complete is retried and a VM with that name exists, the stage fails with
   "name taken" instead of adopting it; inspect the VM and remove or rename it first.
8. The vCenter service account additionally needs *Virtual machine → Change
   Configuration → Advanced configuration* to write the `infraops.job_id` marker.

## Required operational data

- A vCenter FQDN/port, TLS trust chain, least-privilege service account, and its managed
  vCenter credential selected in the UI.
- Access to the vSphere datacenters, clusters, and hosts that operators may target.
- A managed Windows provisioning-administrator credential selected in every workflow.
- Optional domain name, OU path, and a separate managed domain-join credential.
- For blank VMs, a Windows ISO on an accessible datastore, its image index, language,
  keyboard input locale, and time-zone identifier.
- Approved software repository roots plus each application's installer, silent arguments,
  detection rule, timeout, reboot behavior, and dependencies.
- Public root/intermediate CA certificates and their intended LocalMachine stores. Private
  keys are neither needed nor accepted.
- Naming policy, job timeouts, reverse-proxy HTTPS origin, backup/retention policy, and a
  decision on whether the existing ICMP/DNS/vCenter IP conflict checks are sufficient.

## Current integration boundaries

- Authentication is local username/password only. LDAP and OIDC are schema placeholders,
  not implemented providers; production startup rejects selecting them.
- IPAM has no connector and is not exposed as a validation source. Add a real provider
  implementation before relying on IPAM for address allocation/conflict checks.
- The vCenter CA must be trusted inside both backend and worker containers, not only by the
  Ubuntu host. Compose mounts `./certs` (or `VCENTER_CA_DIR`) read-only at
  `/etc/infraops/certs` in both; see *Trusting the vCenter certificate* below.
- A UNC software repository must be reachable from the Windows guest under the account used
  by VMware Tools guest operations. Validate share and NTFS permissions from the template.
- The stored-credential key root is `CREDENTIAL_ENCRYPTION_KEY` (or `SECRET_KEY` when
  unset). Back it up securely and restore the same value during disaster recovery; losing
  it makes existing ciphertext unreadable. Rotate with `python -m app.secrets.rotate`
  while the old root is listed in `CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`.
- The worker exposes `/metrics` and `/health` on `WORKER_METRICS_PORT` (9102) inside the
  Compose network; scrape it alongside the API's `/metrics`.

## HTTPS for the web UI

The session cookie is `Secure` in production, and browsers discard `Secure` cookies on
plain-HTTP pages. Opened over `http://<server>`, InfraOps signs you in but every page
reload signs you out again (the UI shows a warning). Serve it over HTTPS in one of two
ways:

* **Built-in TLS (no other proxy needed):** put the certificate chain and private key in
  `/opt/InfraOps/tls/tls.crt` and `tls.key` (PEM; e.g. issued by your AD CS for the
  server's DNS name), set `FRONTEND_BIND_ADDRESS=0.0.0.0` if users connect from other
  machines, and run `docker compose up -d frontend`. HTTPS is served on
  `FRONTEND_HTTPS_PORT` (default 8443); the HTTP port then only redirects to HTTPS.
* **Host reverse proxy:** leave `./tls` empty and terminate TLS in front of
  `127.0.0.1:8080`, forwarding `Host`, `X-Forwarded-For` and `X-Forwarded-Proto`.

## Trusting the vCenter certificate

Production refuses vCenter connections with TLS verification disabled. With the default
VMCA-signed certificate:

1. Download the VMCA root(s): `curl -k -o vc-certs.zip https://<vcenter-fqdn>/certs/download.zip`
   and unzip; the PEM roots are the `certs/lin/*.0` files. Because this download is not
   yet verified, compare the root's SHA-256 fingerprint
   (`openssl x509 -in <file>.0 -noout -fingerprint -sha256`) with the one shown in the
   vSphere Client under *Administration → Certificates → Certificate Management → Trusted
   Root Certificates*.
2. Concatenate the roots (and any enterprise CA that signed the machine certificate) into
   `/opt/InfraOps/certs/vcenter-ca.pem` on the host.
3. Set `VCENTER_CA_FILE=/etc/infraops/certs/vcenter-ca.pem` in `.env` and run
   `docker compose up -d backend worker`.
4. In **Administration → VMware connections**, edit the connection, enable *Verify the TLS
   certificate* and run *Test connection*. The connection host must be a name present in
   the certificate's subject alternative names (normally the vCenter FQDN) — an IP address
   fails hostname verification.

A missing or unreadable `VCENTER_CA_FILE` is reported as a clear configuration error on
the job and in the logs.

If the certificate cannot be verified and you accept the risk, set
`ALLOW_INSECURE_VCENTER_TLS=true` in `.env`, restart the backend and worker, and leave
*Verify the TLS certificate* unticked on the connection. The connection is still
encrypted, but InfraOps no longer confirms the vCenter's identity; each connection logs a
warning.
