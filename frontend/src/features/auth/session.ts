/** Session helpers shared by the auth context, idle timer and login page. */

import type { SessionPolicy } from '@/types/api'

export const DEFAULT_SESSION_POLICY: SessionPolicy = {
  idle_timeout_seconds: 300,
  idle_warning_seconds: 60,
}

/** Last user activity (epoch ms), shared by every open tab. */
export const LAST_ACTIVITY_KEY = 'infraops.last-activity'
/** Why the previous session ended; shown once on the login page. */
export const SIGNOUT_REASON_KEY = 'infraops.signout-reason'

export type SignOutReason = 'idle'

export type IdleState = 'active' | 'warning' | 'expired'

export function idleState(
  now: number,
  lastActivity: number,
  timeoutMs: number,
  warningMs: number,
): IdleState {
  const idleFor = now - lastActivity
  if (idleFor >= timeoutMs + warningMs) return 'expired'
  if (idleFor >= timeoutMs) return 'warning'
  return 'active'
}

export function readLastActivity(fallback: number): number {
  try {
    const value = Number(localStorage.getItem(LAST_ACTIVITY_KEY))
    return Number.isFinite(value) && value > 0 ? value : fallback
  } catch {
    return fallback
  }
}

export function writeLastActivity(value: number): void {
  try {
    localStorage.setItem(LAST_ACTIVITY_KEY, String(value))
  } catch {
    /* storage unavailable: the timer still works per tab */
  }
}

export function rememberSignOutReason(reason: SignOutReason): void {
  try {
    sessionStorage.setItem(SIGNOUT_REASON_KEY, reason)
  } catch {
    /* ignore */
  }
}

export function peekSignOutReason(): SignOutReason | null {
  try {
    return sessionStorage.getItem(SIGNOUT_REASON_KEY) === 'idle' ? 'idle' : null
  } catch {
    return null
  }
}

export function clearSignOutReason(): void {
  try {
    sessionStorage.removeItem(SIGNOUT_REASON_KEY)
  } catch {
    /* ignore */
  }
}
