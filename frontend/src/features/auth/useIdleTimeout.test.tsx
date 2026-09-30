import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LAST_ACTIVITY_KEY } from '@/features/auth/session'
import { useIdleTimeout } from '@/features/auth/useIdleTimeout'

function setup(onExpire = vi.fn()) {
  const hook = renderHook(() =>
    useIdleTimeout({ enabled: true, timeoutSeconds: 300, warningSeconds: 60, onExpire }),
  )
  return { ...hook, onExpire }
}

describe('useIdleTimeout', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-30T08:00:00Z'))
    localStorage.clear()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('warns after five idle minutes and signs out when nobody answers', () => {
    const { result, onExpire } = setup()

    act(() => vi.advanceTimersByTime(299_000))
    expect(result.current.warning).toBe(false)

    act(() => vi.advanceTimersByTime(2_000))
    expect(result.current.warning).toBe(true)
    expect(result.current.secondsLeft).toBeLessThanOrEqual(60)
    expect(onExpire).not.toHaveBeenCalled()

    act(() => vi.advanceTimersByTime(60_000))
    expect(onExpire).toHaveBeenCalledTimes(1)
  })

  it('activity resets the timer, but not while the warning is showing', () => {
    const { result, onExpire } = setup()

    act(() => vi.advanceTimersByTime(250_000))
    act(() => {
      window.dispatchEvent(new Event('keydown'))
    })
    act(() => vi.advanceTimersByTime(250_000))
    expect(result.current.warning).toBe(false)

    act(() => vi.advanceTimersByTime(60_000))
    expect(result.current.warning).toBe(true)
    act(() => {
      window.dispatchEvent(new Event('pointermove'))
    })
    act(() => vi.advanceTimersByTime(61_000))
    expect(onExpire).toHaveBeenCalledTimes(1)
  })

  it('"Stay signed in" dismisses the warning and restarts the timer', () => {
    const { result, onExpire } = setup()

    act(() => vi.advanceTimersByTime(301_000))
    expect(result.current.warning).toBe(true)
    act(() => result.current.stayActive())
    expect(result.current.warning).toBe(false)

    act(() => vi.advanceTimersByTime(290_000))
    expect(result.current.warning).toBe(false)
    expect(onExpire).not.toHaveBeenCalled()
  })

  it('activity in another tab keeps this tab signed in', () => {
    const { result, onExpire } = setup()

    act(() => vi.advanceTimersByTime(301_000))
    expect(result.current.warning).toBe(true)
    act(() => {
      localStorage.setItem(LAST_ACTIVITY_KEY, String(Date.now()))
      window.dispatchEvent(new StorageEvent('storage', { key: LAST_ACTIVITY_KEY }))
    })
    expect(result.current.warning).toBe(false)
    act(() => vi.advanceTimersByTime(120_000))
    expect(onExpire).not.toHaveBeenCalled()
  })
})
