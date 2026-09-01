/** Compact provisioning workflow navigation. */

import { Check } from 'lucide-react'

import { cn } from '@/lib/utils'

export interface StepperStep {
  key: string
  title: string
}

export type StepState = 'complete' | 'current' | 'upcoming' | 'error'

export function Stepper({
  steps,
  currentIndex,
  errorKeys = [],
  onStepClick,
}: {
  steps: StepperStep[]
  currentIndex: number
  errorKeys?: string[]
  onStepClick?: (index: number) => void
}) {
  const progress = Math.round(((currentIndex + 1) / steps.length) * 100)

  return (
    <nav aria-label="Wizard progress">
      <div className="border-b border-slate-200/90 bg-[#f8fbff] px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
              Deployment workflow
            </p>
            <p className="mt-0.5 text-xs font-medium text-slate-800">
              Step {currentIndex + 1} of {steps.length}
            </p>
          </div>
          <span className="font-mono text-xs font-semibold text-slate-500">{progress}%</span>
        </div>
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-200/70" aria-hidden>
          <div className="h-full bg-brand-600" style={{ width: `${progress}%` }} />
        </div>
      </div>

      <ol className="grid grid-cols-3 gap-1 p-2 sm:grid-cols-5 lg:grid-cols-1">
        {steps.map((step, index) => {
          const hasError = errorKeys.includes(step.key)
          const state: StepState = hasError
            ? 'error'
            : index < currentIndex
              ? 'complete'
              : index === currentIndex
                ? 'current'
                : 'upcoming'
          const clickable = Boolean(onStepClick) && index < currentIndex

          return (
            <li key={step.key}>
              <button
                type="button"
                disabled={!clickable}
                onClick={() => clickable && onStepClick?.(index)}
                aria-current={state === 'current' ? 'step' : undefined}
                className={cn(
                  'animate-row-enter flex min-h-10 w-full items-center gap-2.5 rounded-md border-l-4 border-transparent px-2.5 py-2 text-left transition-all',
                  state === 'current' && 'border-brand-600 bg-brand-50 text-brand-900',
                  state === 'complete' && 'border-transparent bg-white text-slate-700',
                  state === 'upcoming' && 'border-transparent text-slate-400',
                  state === 'error' && 'border-red-500 bg-red-50 text-red-800',
                  clickable && 'cursor-pointer hover:bg-slate-100',
                )}
              >
                <span
                  className={cn(
                    'flex h-5 w-5 shrink-0 items-center justify-center rounded-md border text-[10px] font-bold',
                    state === 'current' && 'border-brand-600 bg-brand-600 text-white',
                    state === 'complete' && 'border-emerald-600 bg-emerald-600 text-white',
                    state === 'upcoming' && 'border-slate-300 bg-white text-slate-400',
                    state === 'error' && 'border-red-600 bg-red-600 text-white',
                  )}
                >
                  {state === 'complete' ? (
                    <Check className="h-3 w-3" />
                  ) : state === 'error' ? (
                    '!'
                  ) : (
                    index + 1
                  )}
                </span>
                <span className={cn('min-w-0 truncate text-xs font-medium', state === 'current' && 'font-semibold')}>
                  {step.title}
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
