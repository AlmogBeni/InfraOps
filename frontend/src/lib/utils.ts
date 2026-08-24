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
  if (!iso) return '—'
  const date = new Date(iso)
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
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString()
}

export function formatGb(value: number | null | undefined): string {
  if (value == null) return '—'
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
  return key
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}
