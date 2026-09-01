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
      <div className="relative flex items-center justify-between border-b border-[#dce4dd] bg-[linear-gradient(135deg,#fbfcf9_0%,#eef5ef_100%)] px-5 py-3">
        <p className="text-xs font-semibold text-[#48534d]">Deployment plan</p>
        <p className="text-[11px] text-[#758079]">Step {currentIndex + 1} of {steps.length} · {progress}% complete</p>
        <span className="absolute bottom-[-1px] left-0 h-0.5 bg-gradient-to-r from-brand-700 via-brand-500 to-[#d8f06a] transition-[width] duration-500 ease-out" style={{ width: `${progress}%` }} aria-hidden />
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
                  'absolute left-[-50%] right-[calc(50%+16px)] top-4 h-px transition-colors duration-500',
                  index <= currentIndex ? 'bg-brand-500' : 'bg-[#d8ddd7]',
                )} aria-hidden />
              )}
              <button
                type="button"
                disabled={!clickable}
                onClick={() => clickable && onStepClick?.(index)}
                aria-current={state === 'current' ? 'step' : undefined}
                className={cn(
                  'relative z-10 mx-auto flex min-w-0 flex-col items-center gap-1.5 rounded-xl px-2 py-1 text-center transition-[transform,background-color] duration-200',
                  clickable && 'cursor-pointer hover:-translate-y-0.5 hover:bg-[#f1f5ef]',
                )}
              >
                <span className={cn(
                  'grid h-7 w-7 place-items-center rounded-full border text-[10px] font-bold transition-[transform,background-color,border-color,box-shadow] duration-300',
                  state === 'current' && 'scale-110 border-brand-800 bg-brand-700 text-white shadow-[0_0_0_4px_rgba(31,109,88,0.12),0_8px_18px_rgba(23,79,64,0.18)]',
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
