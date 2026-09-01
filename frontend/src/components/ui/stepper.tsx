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
    <nav aria-label="Deployment progress">
      <div className="flex items-center justify-between border-b border-[#e1e5e0] bg-[#f8f9f6] px-5 py-3">
        <p className="text-xs font-semibold text-[#48534d]">Deployment plan</p>
        <p className="text-[11px] text-[#758079]">Step {currentIndex + 1} of {steps.length} · {progress}% complete</p>
      </div>
      <ol className="flex min-w-full overflow-x-auto px-3 py-3">
        {steps.map((step, index) => {
          const hasError = errorKeys.includes(step.key)
          const state: StepState = hasError ? 'error' : index < currentIndex ? 'complete' : index === currentIndex ? 'current' : 'upcoming'
          const clickable = Boolean(onStepClick) && index < currentIndex
          return (
            <li key={step.key} className="relative flex min-w-[150px] flex-1 items-center">
              {index > 0 && (
                <span className={cn(
                  'absolute left-[-50%] right-[calc(50%+16px)] top-4 h-px',
                  index <= currentIndex ? 'bg-brand-500' : 'bg-[#d8ddd7]',
                )} aria-hidden />
              )}
              <button
                type="button"
                disabled={!clickable}
                onClick={() => clickable && onStepClick?.(index)}
                aria-current={state === 'current' ? 'step' : undefined}
                className={cn(
                  'relative z-10 mx-auto flex min-w-0 flex-col items-center gap-1.5 rounded-lg px-2 py-1 text-center',
                  clickable && 'cursor-pointer hover:bg-[#f1f4ef]',
                )}
              >
                <span className={cn(
                  'grid h-7 w-7 place-items-center rounded-full border text-[10px] font-bold',
                  state === 'current' && 'border-brand-700 bg-brand-700 text-white shadow-[0_0_0_4px_rgba(31,109,88,0.11)]',
                  state === 'complete' && 'border-brand-600 bg-brand-50 text-brand-700',
                  state === 'upcoming' && 'border-[#cfd5cf] bg-white text-[#8a938d]',
                  state === 'error' && 'border-red-600 bg-red-600 text-white',
                )}>
                  {state === 'complete' ? <Check className="h-3.5 w-3.5" /> : state === 'error' ? '!' : index + 1}
                </span>
                <span className={cn(
                  'max-w-[130px] truncate text-[11px] font-medium',
                  state === 'current' ? 'font-semibold text-[#17201c]' : state === 'error' ? 'text-red-700' : 'text-[#758079]',
                )}>{step.title}</span>
              </button>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
