/** Vertical wizard progress indicator with per-step state. */

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
  return (
    <nav aria-label="Wizard progress" className="space-y-0">
      {steps.map((step, index) => {
        const hasError = errorKeys.includes(step.key)
        const state: StepState =
          hasError && index <= currentIndex ? 'error' : index < currentIndex ? 'complete' : index === currentIndex ? 'current' : 'upcoming'
        const clickable = Boolean(onStepClick) && index < currentIndex

        return (
          <div key={step.key} className="relative flex gap-3 pb-6 last:pb-0">
            {/* Connector */}
            {index < steps.length - 1 && (
              <span
                aria-hidden
                className={cn(
                  'absolute left-[11px] top-6 h-full w-px',
                  index < currentIndex ? 'bg-brand-500' : 'bg-slate-200',
                )}
              />
            )}

            {/* Indicator */}
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onStepClick?.(index)}
              aria-current={state === 'current' ? 'step' : undefined}
              className={cn(
                'relative z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold transition-colors',
                state === 'complete' && 'border-brand-500 bg-brand-500 text-white',
                state === 'current' && 'border-brand-500 bg-white text-brand-600 ring-4 ring-brand-100',
                state === 'error' && 'border-red-500 bg-red-500 text-white',
                state === 'upcoming' && 'border-slate-300 bg-white text-slate-400',
                clickable && 'cursor-pointer hover:bg-brand-50',
              )}
            >
              {state === 'complete' && <Check className="h-3.5 w-3.5" />}
              {state === 'error' ? '!' : index + 1}
            </button>

            {/* Label */}
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onStepClick?.(index)}
              className={cn(
                'text-left text-sm leading-6',
                state === 'current' && 'font-semibold text-slate-900',
                state === 'complete' && 'text-slate-700',
                state === 'upcoming' && 'text-slate-400',
                state === 'error' && 'font-medium text-red-600',
                clickable ? 'cursor-pointer' : 'cursor-default',
              )}
            >
              {step.title}
            </button>
          </div>
        )
      })}
    </nav>
  )
}
