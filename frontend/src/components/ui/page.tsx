import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  meta,
  className,
}: {
  eyebrow: string
  title: string
  description: string
  actions?: ReactNode
  meta?: ReactNode
  className?: string
}) {
  return (
    <header className={cn('border-b border-slate-300 pb-4', className)}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="console-kicker">{eyebrow}</p>
          <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">{title}</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-600">{description}</p>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {meta && <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-500">{meta}</div>}
    </header>
  )
}

export function PanelHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 bg-[#f5f7f9] px-4 py-2.5">
      <div>
        <h3 className="text-xs font-bold uppercase tracking-[0.08em] text-slate-700">{title}</h3>
        {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
      </div>
      {actions}
    </div>
  )
}

export function ConsolePanel({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section className={cn('overflow-hidden rounded-md border border-slate-300 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]', className)}>
      {children}
    </section>
  )
}

export function DataPoint({ label, value, detail }: { label: string; value: ReactNode; detail?: string }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-slate-500">{label}</p>
      <p className="mt-1 truncate text-xl font-semibold tabular-nums tracking-tight text-slate-950">{value}</p>
      {detail && <p className="mt-0.5 truncate text-[11px] text-slate-500">{detail}</p>}
    </div>
  )
}
