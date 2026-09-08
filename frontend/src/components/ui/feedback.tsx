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
    neutral: 'bg-[#eef1ed] text-[#59635d] ring-[#d9dfd9]',
    success: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
    warning: 'bg-amber-50 text-amber-900 ring-amber-200',
    danger: 'bg-red-50 text-red-800 ring-red-200',
    info: 'bg-brand-50 text-brand-800 ring-brand-200',
    running: 'bg-[#fff5ed] text-[#a34527] ring-[#f4c7aa]',
  }
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold ring-1 ring-inset',
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
  ACTION_REQUIRED: { label: 'Action Required', tone: 'warning', icon: AlertTriangleIcon },
  FAILED: { label: 'Failed', tone: 'danger', icon: FailIcon },
  CANCELLED: { label: 'Cancelled', tone: 'neutral', icon: FailIcon },
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const meta = JOB_STATUS_META[status] ?? JOB_STATUS_META.QUEUED
  const Icon = meta.icon
  return (
    <Badge tone={meta.tone}>
      <Icon aria-hidden className={cn('h-3 w-3', status === 'RUNNING' && 'ui-spinner')} />
      {meta.label}
    </Badge>
  )
}

const STEP_STATUS_META: Record<StepStatus, { icon: typeof OkIcon; classes: string }> = {
  PENDING: { icon: CircleDashed, classes: 'text-slate-400' },
  RUNNING: { icon: Loader2, classes: 'text-sky-600 ui-spinner' },
  SUCCEEDED: { icon: OkIcon, classes: 'text-emerald-600' },
  FAILED: { icon: FailIcon, classes: 'text-red-600' },
  SKIPPED: { icon: CircleDashed, classes: 'text-slate-400' },
  WARNING: { icon: AlertTriangleIcon, classes: 'text-amber-600' },
  WAITING_FOR_PREREQUISITE: { icon: CircleDashed, classes: 'text-amber-600' },
  NOT_APPLICABLE: { icon: CircleDashed, classes: 'text-slate-400' },
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
    <div className={cn('rounded-xl border px-4 py-3 text-sm', tones[tone])} role="alert">
      <div className="flex items-start gap-2">
        <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
        <div>
          {title && <p className="font-semibold tracking-[-0.01em]">{title}</p>}
          <div className={cn('text-xs leading-5', title && 'mt-1')}>{children}</div>
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
        className="h-2 w-full overflow-hidden rounded-full bg-[#e3e7e2]"
        role="progressbar"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label ?? 'Progress'}
      >
        <div
          className="h-full bg-gradient-to-r from-brand-500 to-brand-700 transition-[width] duration-300"
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
    <Loader2 aria-label="Loading" className={cn('ui-spinner h-5 w-5 text-slate-400', className)} />
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
    <div className="flex min-h-56 flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-[#cfd5cf] bg-white/60 px-6 py-10 text-center">
      <div className="rounded-2xl border border-[#d8ddd7] bg-[#f5f7f3] p-3">
        <CircleDashed aria-hidden className="h-6 w-6 text-[#7c8680]" />
      </div>
      <p className="mt-1 text-sm font-semibold text-[#2b342f]">{title}</p>
      {description && <p className="max-w-md text-xs leading-5 text-[#6b756f]">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function LoadingState({
  title,
  description,
  fullPage = false,
}: {
  title: string
  description?: string
  fullPage?: boolean
}) {
  return (
    <div
      className={cn(
        'flex items-center justify-center px-6 text-center',
        fullPage ? 'min-h-screen' : 'min-h-48 rounded-2xl border border-[#d8ddd7] bg-white',
      )}
      role="status"
    >
      <div className="max-w-sm">
        <span className="mx-auto grid h-11 w-11 place-items-center rounded-2xl bg-brand-50 text-brand-700">
          <Loader2 className="ui-spinner h-5 w-5" aria-hidden />
        </span>
        <p className="mt-3 text-sm font-semibold text-[#202923]">{title}</p>
        {description && <p className="mt-1 text-xs leading-5 text-[#6b756f]">{description}</p>}
      </div>
    </div>
  )
}
