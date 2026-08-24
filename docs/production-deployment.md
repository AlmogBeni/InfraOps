# Production deployment

InfraOps now fails closed when production security settings are missing. The runtime creates
no sample infrastructure, certificates, applications, credentials, or demo accounts.

## First start

1. Back up PostgreSQL if upgrading an existing deployment. Migration
   `0002_remove_dev_seed_data` deletes only the former seed records identified by
   their exact IDs, email addresses, descriptions, or installer paths.
2. Copy `.env.example` to `.env`, fill the required values, and run `chmod 600 .env`.
3. Set a one-time bootstrap administrator username and password. Run
   `docker compose up --build` and confirm that administrator can log in.
4. Remove `BOOTSTRAP_ADMIN_PASSWORD` from `.env`, restart, and retain the password in your
   normal enterprise password manager.
5. Through Administration, add the vCenter connection and site mapping, then test the
   connection before creating any provisioning job.

The frontend listens on `127.0.0.1:8080` by default for a host-level TLS reverse proxy. The
backend is available only on the private Compose network. Forward the original `Host`,
`X-Forwarded-For`, and `X-Forwarded-Proto` headers and preserve long-lived, unbuffered SSE
connections under `/api/v1/provisioning/jobs/*/events`.

## Required operational data

- A vCenter FQDN/port, TLS trust chain, least-privilege service account, and the secret names
  holding its username/password.
- A site name and the vSphere datacenter managed-object reference it maps to.
- The template-local Windows administrator secret at
  `guest-local-admin/username` and `guest-local-admin/password`.
- Optional domain name, OU path, and separate domain-join credential secret.
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
  Ubuntu host. Mount or bake the CA into the container trust store before enabling TLS
  verification.
- A UNC software repository must be reachable from the Windows guest under the account used
  by VMware Tools guest operations. Validate share and NTFS permissions from the template.
