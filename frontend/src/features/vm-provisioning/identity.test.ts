import { describe, expect, it } from 'vitest'

import {
  deriveGuestIdentity,
  derivePersistedGuestIdentity,
  guestIdentityReviewRows,
} from '@/features/vm-provisioning/identity'

describe('domain-joined guest identity', () => {
  it('derives the FQDN from the VM name and normalized domain', () => {
    expect(
      deriveGuestIdentity('srvildc55', 'IGNORED-CUSTOM-NAME', ' Corp.DeltaGalil.com. '),
    ).toEqual({
      computerName: 'SRVILDC55',
      fqdn: 'srvildc55.corp.deltagalil.com',
    })
  })

  it('provides the effective FQDN to the review summary', () => {
    expect(guestIdentityReviewRows('srvildc55', 'OTHER', 'corp.deltagalil.com')).toEqual([
      { label: 'Computer name', value: 'SRVILDC55' },
      { label: 'Fully qualified DNS name', value: 'srvildc55.corp.deltagalil.com' },
      { label: 'Domain membership', value: 'corp.deltagalil.com' },
    ])
  })

  it('preserves an explicit standalone computer name when no join is requested', () => {
    expect(deriveGuestIdentity('inventory-name', 'guest-name', null)).toEqual({
      computerName: 'GUEST-NAME',
      fqdn: null,
    })
  })

  it('reports the stored identity truthfully for a legacy domain-join request', () => {
    expect(
      derivePersistedGuestIdentity('inventory-name', 'LEGACY-HOST', 'corp.example.com'),
    ).toEqual({
      computerName: 'LEGACY-HOST',
      fqdn: 'legacy-host.corp.example.com',
    })
  })
})
