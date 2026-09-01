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
    <header className={cn('animate-panel-reveal pb-1', className)}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="console-kicker">{eyebrow}</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-[-0.035em] text-[#17201c]">{title}</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[#68736d]">{description}</p>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {meta && <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-[#758079]">{meta}</div>}
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
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#dce4dd] bg-[linear-gradient(135deg,#fbfcf9_0%,#eef5ef_100%)] px-5 py-4">
      <div>
        <h3 className="text-sm font-semibold tracking-[-0.01em] text-[#202923]">{title}</h3>
        {description && <p className="mt-1 text-xs leading-5 text-[#6b756f]">{description}</p>}
      </div>
      {actions}
    </div>
  )
}

export function ConsolePanel({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section
      className={cn(
        'animate-panel-reveal overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)] transition-[border-color,box-shadow] duration-300 hover:border-[#c5d0c7] hover:shadow-[0_2px_4px_rgba(23,32,28,0.04),0_18px_42px_rgba(23,79,64,0.08)]',
        className,
      )}
    >
      {children}
    </section>
  )
}

export function DataPoint({ label, value, detail }: { label: string; value: ReactNode; detail?: string }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <p className="text-[10px] font-bold uppercase tracking-[0.11em] text-[#7b857f]">{label}</p>
      <p className="mt-2 truncate text-2xl font-semibold tabular-nums tracking-[-0.03em] text-[#17201c]">{value}</p>
      {detail && <p className="mt-1 truncate text-[11px] text-[#758079]">{detail}</p>}
    </div>
  )
}
