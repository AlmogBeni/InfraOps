import { describe, expect, it } from 'vitest'

import { idleState, refreshCookieWillBeDropped } from '@/features/auth/session'

describe('idle state', () => {
  const timeout = 300_000
  const warning = 60_000

  it('warns after the timeout and expires after the warning period', () => {
    expect(idleState(299_999, 0, timeout, warning)).toBe('active')
    expect(idleState(300_000, 0, timeout, warning)).toBe('warning')
    expect(idleState(359_999, 0, timeout, warning)).toBe('warning')
    expect(idleState(360_000, 0, timeout, warning)).toBe('expired')
  })

  it('expires immediately after a long suspension', () => {
    expect(idleState(3_600_000, 0, timeout, warning)).toBe('expired')
  })
})

describe('insecure transport detection', () => {
  const secure = { cookie_secure: true }

  it('flags plain HTTP to a server name or address', () => {
    expect(refreshCookieWillBeDropped(secure, { protocol: 'http:', hostname: 'infraops.corp.local' })).toBe(true)
    expect(refreshCookieWillBeDropped(secure, { protocol: 'http:', hostname: '10.1.2.3' })).toBe(true)
  })

  it('accepts HTTPS, loopback, and non-secure cookie configurations', () => {
    expect(refreshCookieWillBeDropped(secure, { protocol: 'https:', hostname: 'infraops.corp.local' })).toBe(false)
    expect(refreshCookieWillBeDropped(secure, { protocol: 'http:', hostname: 'localhost' })).toBe(false)
    expect(refreshCookieWillBeDropped(secure, { protocol: 'http:', hostname: '127.0.0.1' })).toBe(false)
    expect(refreshCookieWillBeDropped({ cookie_secure: false }, { protocol: 'http:', hostname: '10.1.2.3' })).toBe(false)
  })
})
