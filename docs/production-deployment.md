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
6. vCenter connections with *Verify the TLS certificate* ticked need a trusted certificate
   (system trust store or `VCENTER_CA_FILE`); untick it for a self-signed vCenter.
7. VMs created before the upgrade carry no ownership marker. If an old job whose VM-creation
   stage did not complete is retried and a VM with that name exists, the stage fails with
   "name taken" instead of adopting it; inspect the VM and remove or rename it first.
8. The vCenter service account additionally needs *Virtual machine → Change
   Configuration → Advanced configuration* to write the `infraops.job_id` marker.

## Upgrading to the ISO-only release (migration 0009)

1. Back up PostgreSQL, stop the worker, then run `migrate` as usual.
2. The migration renames the VM-creation stage to `create_vm` and its timeout setting to
   `create_vm_minutes`, and removes the `ACTION_REQUIRED` job status, the
   `WAITING_FOR_PREREQUISITE` step status and the `action_required` column.
3. Jobs that were paused for an administrator become `PARTIALLY_COMPLETED` (or `FAILED` when
   no VM exists yet) with the paused stage `FAILED`, so it can be retried unattended. Their
   pause text moves to the job's technical detail.
4. Queued, running, interrupted or paused jobs of removed workflows (package deployments and
   blank VMs without an ISO) are closed as `FAILED`/`PARTIALLY_COMPLETED` with an explanation.
   All such jobs stay viewable but cannot be retried; submit new requests. Audit events and
   stored requests are not modified.
5. vCenter privileges — add: *Virtual machine › Edit inventory › Create new*; *Change
   configuration › Add new disk, Add or remove device, Modify device settings, Change
   settings, Set annotation*; *Interaction › Configure CD media, Connect devices, Answer
   question, Create screenshot, Inject USB HID scan codes, Reset*; *Datastore › Low level file
   operations, Remove file*. Remove, if nothing else needs them: every *Virtual machine ›
   Provisioning* privilege, *Interaction › Install VMware Tools*, *vApp › Import* and the
   library privileges that package deployment used. See
   [vmware-integration.md](vmware-integration.md#required-vcenter-privileges-least-privilege).
6. Place a Windows Server ISO on a datastore of every datacenter you deploy to. Store retail
   or MAK keys as credentials with purpose *Windows product key*.
7. Browser drafts of the previous wizard are discarded once.

## Required operational data

- A vCenter FQDN/port, TLS trust chain, least-privilege service account, and its managed
  vCenter credential selected in the UI.
- Access to the vSphere datacenters, clusters, and hosts that operators may target.
- HTTPS (TCP 443) from the backend and worker containers to **every ESXi host**, with host
  names that resolve inside the containers. VMware Tools file transfers — every in-guest
  configuration step — go directly to the host that runs the VM, not through vCenter. With
  *Verify the TLS certificate* ticked, `VCENTER_CA_FILE` must also contain the CA that signs
  the ESXi host certificates (normally the vCenter VMCA root). Preflight checks this.
- A managed Windows provisioning-administrator credential selected in every workflow.
- Optional domain name, OU path, and a separate managed domain-join credential.
- A Windows Server ISO on a datastore of each target datacenter (the only manual
  prerequisite), the edition (Standard or Datacenter, always with the Desktop
  Experience), language, keyboard input locale and time-zone
  identifier, and — for retail or MAK media — a product key stored as a credential with
  purpose *Windows product key*. The ESXi hosts must provide the VMware Tools ISO
  (`[] /vmimages/tools-isoimages/windows.iso`), which they do by default.
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
  by VMware Tools guest operations. Validate share and NTFS permissions from a test VM.
- The stored-credential key root is `CREDENTIAL_ENCRYPTION_KEY` (or `SECRET_KEY` when
  unset). Back it up securely and restore the same value during disaster recovery; losing
  it makes existing ciphertext unreadable. Rotate with `python -m app.secrets.rotate`
  while the old root is listed in `CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`.
- The worker exposes `/metrics` and `/health` on `WORKER_METRICS_PORT` (9102) inside the
  Compose network; scrape it alongside the API's `/metrics`.

## HTTPS for the web UI

With `COOKIE_SECURE=true` the session cookie is `Secure` whenever the browser uses HTTPS.
Browsers discard `Secure` cookies on plain-HTTP pages, so over `http://<server>` it is
issued without that flag: sessions still survive a reload, but the refresh token, like the
password and all other traffic, crosses the network unencrypted. Serve InfraOps over HTTPS
in one of two ways:

* **Built-in TLS (no other proxy needed):** put the certificate chain and private key in
  `/opt/InfraOps/tls/tls.crt` and `tls.key` (PEM; e.g. issued by your AD CS for the
  server's DNS name), set `FRONTEND_BIND_ADDRESS=0.0.0.0` if users connect from other
  machines, and run `docker compose up -d frontend`. HTTPS is served on
  `FRONTEND_HTTPS_PORT` (default 8443); the HTTP port then only redirects to HTTPS.
* **Host reverse proxy:** leave `./tls` empty and terminate TLS in front of
  `127.0.0.1:8080`, forwarding `Host`, `X-Forwarded-For` and `X-Forwarded-Proto`.

## Trusting the vCenter certificate

To verify the default VMCA-signed certificate:

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

If the certificate cannot be verified and you accept the risk, untick *Verify the TLS
certificate* on the connection. The connection is still encrypted, but InfraOps no longer
confirms the vCenter's identity; each connection logs a warning.
