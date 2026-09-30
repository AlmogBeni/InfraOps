/** Tracks user inactivity across all open tabs. */

import { useCallback, useEffect, useRef, useState } from 'react'

import { idleState, readLastActivity, writeLastActivity, LAST_ACTIVITY_KEY } from '@/features/auth/session'

const ACTIVITY_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'scroll', 'touchstart'] as const
const WRITE_THROTTLE_MS = 5_000
const TICK_MS = 1_000

export interface IdleTimeout {
  warning: boolean
  secondsLeft: number
  stayActive: () => void
}

/**
 * After `timeoutSeconds` without keyboard/pointer activity in any tab the hook
 * enters the warning state; `onExpire` fires when `warningSeconds` more pass.
 * While the warning is shown only an explicit `stayActive()` (or activity in
 * another tab) resumes the session. Elapsed time is computed from timestamps,
 * so throttled background tabs and a suspended laptop are handled correctly.
 */
export function useIdleTimeout({
  enabled,
  timeoutSeconds,
  warningSeconds,
  onExpire,
}: {
  enabled: boolean
  timeoutSeconds: number
  warningSeconds: number
  onExpire: () => void
}): IdleTimeout {
  const [warning, setWarning] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState(warningSeconds)
  const warningRef = useRef(false)
  const lastWriteRef = useRef(0)
  const expiredRef = useRef(false)
  const onExpireRef = useRef(onExpire)
  onExpireRef.current = onExpire

  const markActive = useCallback((force = false) => {
    const now = Date.now()
    if (!force && now - lastWriteRef.current < WRITE_THROTTLE_MS) return
    lastWriteRef.current = now
    writeLastActivity(now)
  }, [])

  const stayActive = useCallback(() => {
    warningRef.current = false
    setWarning(false)
    markActive(true)
  }, [markActive])

  useEffect(() => {
    if (!enabled) return
    expiredRef.current = false
    markActive(true)
    const timeoutMs = timeoutSeconds * 1000
    const warningMs = warningSeconds * 1000

    const onActivity = () => {
      if (!warningRef.current) markActive()
    }
    const tick = () => {
      const now = Date.now()
      const state = idleState(now, readLastActivity(now), timeoutMs, warningMs)
      if (state === 'expired') {
        if (!expiredRef.current) {
          expiredRef.current = true
          onExpireRef.current()
        }
        return
      }
      const inWarning = state === 'warning'
      if (inWarning !== warningRef.current) {
        warningRef.current = inWarning
        setWarning(inWarning)
      }
      if (inWarning) {
        const remaining = timeoutMs + warningMs - (now - readLastActivity(now))
        setSecondsLeft(Math.max(0, Math.ceil(remaining / 1000)))
      }
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === LAST_ACTIVITY_KEY) tick()
    }

    for (const name of ACTIVITY_EVENTS) window.addEventListener(name, onActivity, { passive: true })
    window.addEventListener('storage', onStorage)
    document.addEventListener('visibilitychange', tick)
    const interval = window.setInterval(tick, TICK_MS)
    return () => {
      for (const name of ACTIVITY_EVENTS) window.removeEventListener(name, onActivity)
      window.removeEventListener('storage', onStorage)
      document.removeEventListener('visibilitychange', tick)
      window.clearInterval(interval)
      warningRef.current = false
      setWarning(false)
    }
  }, [enabled, timeoutSeconds, warningSeconds, markActive])

  return { warning, secondsLeft, stayActive }
}
