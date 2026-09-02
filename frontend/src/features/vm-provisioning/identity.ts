export interface EffectiveGuestIdentity {
  computerName: string
  fqdn: string | null
}

export interface GuestIdentityReviewRow {
  label: string
  value: string
}

export function normalizeDomain(domain: string): string {
  return domain.trim().replace(/\.+$/, '').toLowerCase()
}

/**
 * Domain-joined guests use the VM inventory name as their Windows/AD computer
 * name. The FQDN is display metadata; only the short name is sent to Windows.
 */
export function deriveGuestIdentity(
  vmName: string,
  hostname: string | null | undefined,
  domain: string | null | undefined,
): EffectiveGuestIdentity {
  const normalizedDomain = domain ? normalizeDomain(domain) : ''
  const shortName = (normalizedDomain ? vmName : (hostname || vmName)).toUpperCase()
  return {
    computerName: shortName,
    fqdn: normalizedDomain ? `${vmName}.${normalizedDomain}`.toLowerCase() : null,
  }
}

/** Read a persisted request without rewriting the identity chosen by older clients. */
export function derivePersistedGuestIdentity(
  vmName: string,
  hostname: string | null | undefined,
  domain: string | null | undefined,
): EffectiveGuestIdentity {
  const normalizedDomain = domain ? normalizeDomain(domain) : ''
  const computerName = (hostname || vmName).toUpperCase()
  return {
    computerName,
    fqdn: normalizedDomain ? `${computerName}.${normalizedDomain}`.toLowerCase() : null,
  }
}

export function guestIdentityReviewRows(
  vmName: string,
  hostname: string | null | undefined,
  domain: string | null | undefined,
): GuestIdentityReviewRow[] {
  const identity = deriveGuestIdentity(vmName, hostname, domain)
  return [
    { label: 'Computer name', value: identity.computerName },
    ...(identity.fqdn
      ? [{ label: 'Fully qualified DNS name', value: identity.fqdn }]
      : []),
    { label: 'Domain membership', value: domain ? normalizeDomain(domain) : 'No domain join' },
  ]
}
