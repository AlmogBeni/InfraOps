import { describe, expect, it } from 'vitest'

import { idleState } from '@/features/auth/session'

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
