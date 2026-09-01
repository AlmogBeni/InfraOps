/** Status indicators, alerts, progress and empty states.
 * Status is never conveyed by colour alone — every badge pairs an icon with text. */

import {
  AlertTriangle as AlertTriangleIcon,
  CheckCircle2 as OkIcon,
  CircleDashed,
  Info,
  Loader2,
  XCircle as FailIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import type { CheckStatus, JobStatus, StepStatus } from '@/types/api'

// ── Badge ────────────────────────────────────────────────────────────────────

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'running'
  className?: string
}) {
  const tones: Record<string, string> = {
    neutral: 'bg-slate-100/80 text-slate-700 ring-slate-200',
    success: 'bg-emerald-100/80 text-emerald-800 ring-emerald-200',
    warning: 'bg-amber-100/80 text-amber-800 ring-amber-200',
    danger: 'bg-red-100/80 text-red-800 ring-red-200',
    info: 'bg-brand-100 text-brand-800 ring-brand-200',
    running: 'bg-cyan-100 text-cyan-700 ring-cyan-200',
  }
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

const JOB_STATUS_META: Record<JobStatus, { label: string; tone: Parameters<typeof Badge>[0]['tone']; icon: typeof OkIcon }> = {
  QUEUED: { label: 'Queued', tone: 'neutral', icon: CircleDashed },
  RUNNING: { label: 'Running', tone: 'running', icon: Loader2 },
  COMPLETED: { label: 'Completed', tone: 'success', icon: OkIcon },
  PARTIALLY_COMPLETED: { label: 'Partially Completed', tone: 'warning', icon: AlertTriangleIcon },
  FAILED: { label: 'Failed', tone: 'danger', icon: FailIcon },
  CANCELLED: { label: 'Cancelled', tone: 'neutral', icon: FailIcon },
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const meta = JOB_STATUS_META[status] ?? JOB_STATUS_META.QUEUED
  const Icon = meta.icon
  return (
    <Badge tone={meta.tone}>
      <Icon aria-hidden className={cn('h-3 w-3', status === 'RUNNING' && 'animate-spin')} />
      {meta.label}
    </Badge>
  )
}

const STEP_STATUS_META: Record<StepStatus, { icon: typeof OkIcon; classes: string }> = {
  PENDING: { icon: CircleDashed, classes: 'text-slate-400' },
  RUNNING: { icon: Loader2, classes: 'text-sky-600 animate-spin' },
  SUCCEEDED: { icon: OkIcon, classes: 'text-emerald-600' },
  FAILED: { icon: FailIcon, classes: 'text-red-600' },
  SKIPPED: { icon: CircleDashed, classes: 'text-slate-400' },
  CANCELLED: { icon: FailIcon, classes: 'text-slate-500' },
}

export function StepStatusIcon({ status }: { status: StepStatus }) {
  const meta = STEP_STATUS_META[status] ?? STEP_STATUS_META.PENDING
  const Icon = meta.icon
  return <Icon aria-hidden className={cn('h-4 w-4 shrink-0', meta.classes)} />
}

export function CheckStatusBadge({ status }: { status: CheckStatus }) {
  if (status === 'PASS')
    return (
      <Badge tone="success">
        <OkIcon className="h-3 w-3" /> Pass
      </Badge>
    )
  if (status === 'WARN')
    return (
      <Badge tone="warning">
        <AlertTriangleIcon className="h-3 w-3" /> Warning
      </Badge>
    )
  return (
    <Badge tone="danger">
      <FailIcon className="h-3 w-3" /> Failed
    </Badge>
  )
}

// ── Alert ────────────────────────────────────────────────────────────────────

export function Alert({
  tone = 'info',
  title,
  children,
}: {
  tone?: 'info' | 'warning' | 'danger' | 'success'
  title?: string
  children: ReactNode
}) {
  const tones = {
    info: 'border-sky-200 bg-sky-50 text-sky-900',
    warning: 'border-amber-200 bg-amber-50 text-amber-900',
    danger: 'border-red-200 bg-red-50 text-red-900',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  }
  const icons = { info: Info, warning: AlertTriangleIcon, danger: FailIcon, success: OkIcon }
  const Icon = icons[tone]
  return (
    <div className={cn('rounded-md border px-3 py-2 text-sm', tones[tone])} role="alert">
      <div className="flex items-start gap-2">
        <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
        <div>
          {title && <p className="font-semibold">{title}</p>}
          <div className={title ? 'mt-0.5' : ''}>{children}</div>
        </div>
      </div>
    </div>
  )
}

// ── Progress ─────────────────────────────────────────────────────────────────

export function ProgressBar({ percent, label }: { percent: number; label?: string }) {
  const clamped = Math.max(0, Math.min(100, percent))
  return (
    <div>
      <div
        className="h-2 w-full overflow-hidden rounded-sm bg-slate-200"
        role="progressbar"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label ?? 'Progress'}
      >
        <div
          className="h-full animate-[pulse_1.6s_ease-in-out_infinite] bg-gradient-to-r from-brand-500 to-brand-700 transition-[width] duration-300"
          style={{ width: `${clamped}%` }}
        />
      </div>
      {label && <p className="mt-1 text-xs text-slate-500">{label}</p>}
    </div>
  )
}

// ── Misc ─────────────────────────────────────────────────────────────────────

export function Spinner({ className }: { className?: string }) {
  return (
    <Loader2 aria-label="Loading" className={cn('h-5 w-5 animate-spin text-slate-400', className)} />
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 px-5 py-7 text-center">
      <div className="rounded-full border border-[#d6def0] bg-[#f7faff] p-2">
        <CircleDashed aria-hidden className="h-6 w-6 text-slate-400" />
      </div>
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {description && <p className="max-w-md text-xs text-slate-500">{description}</p>}
      {action}
    </div>
  )
}
