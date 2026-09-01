import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  const total = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(total / 60)
  const secs = total % 60
  if (minutes === 0) return `${secs}s`
  if (minutes < 60) return `${minutes}m ${secs.toString().padStart(2, '0')}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${(minutes % 60).toString().padStart(2, '0')}m`
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return 'Not available'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Not available'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return 'Not available'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? 'Not available' : date.toLocaleTimeString()
}

export function formatGb(value: number | null | undefined): string {
  if (value == null) return 'Not available'
  if (value >= 1024) return `${(value / 1024).toFixed(1)} TB`
  return `${value.toFixed(0)} GB`
}

export function maskToPrefix(mask: string): number | null {
  const parts = mask.split('.').map(Number)
  if (parts.length !== 4 || parts.some((p) => Number.isNaN(p) || p < 0 || p > 255)) return null
  const binary = parts.map((p) => p.toString(2).padStart(8, '0')).join('')
  if (!/^1*0*$/.test(binary)) return null
  return binary.indexOf('0') === -1 ? 32 : binary.indexOf('0')
}

export function prefixToMask(prefix: number): string {
  const binary = '1'.repeat(prefix).padEnd(32, '0')
  const octets = binary.match(/.{8}/g) ?? []
  return octets.map((o) => parseInt(o, 2)).join('.')
}

export function humanizeStageKey(key: string | null | undefined): string {
  if (!key) return '—'
  return humanizeIdentifier(key)
}

/** Convert a datetime-local control value from the operator's timezone to UTC. */
export function toApiDateTime(value: string | null | undefined): string {
  if (!value) return ''
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString()
}

export function humanizeIdentifier(value: string | null | undefined): string {
  if (!value) return 'Not available'
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[._\-\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(' ')
}

export function formatBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'Size not available'
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = -1
  do {
    size /= 1024
    unit += 1
  } while (size >= 1024 && unit < units.length - 1)
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`
}

export function displayValue(
  value: unknown,
  fallback = 'Not available',
): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed || ['undefined', 'null', '[object Object]'].includes(trimmed)) return fallback
    return trimmed
  }
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : fallback
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (Array.isArray(value)) return value.length ? value.map((item) => displayValue(item)).join(', ') : fallback
  return fallback
}

const ACTION_LABELS: Record<string, string> = {
  VM_CREATED: 'VM created',
  JOB_SUBMITTED: 'Deployment submitted',
  JOB_COMPLETED: 'Deployment completed',
  JOB_FAILED: 'Deployment failed',
  JOB_CANCELLED: 'Deployment cancelled',
  JOB_RETRIED: 'Deployment retried',
  DRY_RUN_PERFORMED: 'Configuration validated',
  IP_CONFLICT_CHECK_PERFORMED: 'IP address checked',
  LOGIN_SUCCEEDED: 'Signed in',
  LOGIN_FAILED: 'Sign-in failed',
  LOGOUT: 'Signed out',
}

export function formatAction(value: string | null | undefined): string {
  if (!value) return 'Activity recorded'
  return ACTION_LABELS[value] ?? humanizeIdentifier(value)
}
