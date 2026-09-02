import { describe, expect, it } from 'vitest'

import { isValidSecretReference } from '@/features/admin/secret-reference'

describe('secret reference contract', () => {
  it('accepts safe provider paths and rejects ambiguous path segments', () => {
    expect(isValidSecretReference('vcenter/production/username')).toBe(true)
    expect(isValidSecretReference('domain-join')).toBe(true)
    expect(isValidSecretReference('a')).toBe(false)
    expect(isValidSecretReference('/vcenter/username')).toBe(false)
    expect(isValidSecretReference('vcenter//username')).toBe(false)
    expect(isValidSecretReference('vcenter/_username')).toBe(false)
    expect(isValidSecretReference('vcenter/../username')).toBe(false)
  })
})
