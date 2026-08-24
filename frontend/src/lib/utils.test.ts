import { describe, expect, it } from 'vitest'

import { formatDuration, humanizeStageKey, maskToPrefix, prefixToMask } from '@/lib/utils'

describe('subnet helpers', () => {
  it.each([
    ['255.255.255.0', 24],
    ['255.255.0.0', 16],
    ['255.0.0.0', 8],
    ['255.255.255.128', 25],
  ])('mask %s → /%s', (mask, prefix) => {
    expect(maskToPrefix(mask)).toBe(prefix)
  })

  it('rejects non-contiguous masks', () => {
    expect(maskToPrefix('255.0.255.0')).toBeNull()
  })

  it('round-trips prefix ↔ mask', () => {
    expect(prefixToMask(24)).toBe('255.255.255.0')
    expect(maskToPrefix(prefixToMask(25))).toBe(25)
  })
})

describe('formatDuration', () => {
  it('formats seconds/minutes/hours', () => {
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(42)).toBe('42s')
    expect(formatDuration(522)).toBe('8m 42s')
    expect(formatDuration(3661)).toBe('1h 01m')
  })
})

describe('humanizeStageKey', () => {
  it('prettifies stage keys', () => {
    expect(humanizeStageKey('wait_for_tools')).toBe('Wait For Tools')
    expect(humanizeStageKey(null)).toBe('—')
  })
})
