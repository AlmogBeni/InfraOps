/** Decides which notifications are new to this browser and pops them up. */

import type { NotificationOut } from '@/types/api'

/** Ids already announced in this browser, so a reload does not repeat them. */
export const ANNOUNCED_KEY = 'infraops.announced-notifications'
const MAX_REMEMBERED = 200
/** At most this many pop-ups at once; the rest wait in the bell. */
export const MAX_POPUPS = 3

export function readAnnounced(): Set<string> {
  try {
    const raw = localStorage.getItem(ANNOUNCED_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(parsed) ? parsed.filter((id) => typeof id === 'string') : [])
  } catch {
    return new Set()
  }
}

export function rememberAnnounced(ids: string[]): void {
  if (ids.length === 0) return
  try {
    const merged = [...readAnnounced(), ...ids]
    localStorage.setItem(ANNOUNCED_KEY, JSON.stringify(merged.slice(-MAX_REMEMBERED)))
  } catch {
    /* storage unavailable: pop-ups may repeat after a reload */
  }
}

/** Unread notifications this browser has not shown yet, newest first. */
export function unannounced(items: NotificationOut[], announced: Set<string>): NotificationOut[] {
  return items
    .filter((item) => item.read_at === null && !announced.has(item.id))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
}

export type DesktopAlertState = 'unsupported' | 'insecure' | 'default' | 'granted' | 'denied'

/** Browsers offer desktop notifications only on HTTPS (or localhost) pages. */
export function desktopAlertState(): DesktopAlertState {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  if (!window.isSecureContext) return 'insecure'
  return Notification.permission
}

export function showDesktopAlert(item: NotificationOut, onClick: () => void): void {
  if (desktopAlertState() !== 'granted') return
  try {
    // The tag lets the OS collapse the same alert raised by several tabs.
    const alert = new Notification(item.title, { body: item.message, tag: item.id })
    alert.onclick = () => {
      window.focus()
      onClick()
      alert.close()
    }
  } catch {
    /* some browsers only allow notifications from a service worker */
  }
}
