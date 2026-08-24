/** Form control primitives (labelled inputs, selects, checkboxes, radios). */

import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'

import { cn } from '@/lib/utils'

const baseControl =
  'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm ' +
  'placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 ' +
  'disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(baseControl, 'h-9', className)} {...props} />
  },
)

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, rows = 3, ...props }, ref) {
    return <textarea ref={ref} rows={rows} className={cn(baseControl, className)} {...props} />
  },
)

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...props }, ref) {
    return (
      <select ref={ref} className={cn(baseControl, 'h-9 pr-8', className)} {...props}>
        {children}
      </select>
    )
  },
)

interface FormRowProps {
  label: string
  htmlFor?: string
  hint?: string
  error?: string
  required?: boolean
  children: ReactNode
  className?: string
}

export function FormRow({ label, htmlFor, hint, error, required, children, className }: FormRowProps) {
  return (
    <div className={cn('mb-4', className)}>
      <label className="field-label" htmlFor={htmlFor}>
        {label}
        {required && <span className="ml-0.5 text-red-500">*</span>}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      {error && (
        <p className="mt-1 text-xs font-medium text-red-600" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: ReactNode
}

export function Checkbox({ label, className, id, ...props }: CheckboxProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  return (
    <div className={cn('flex items-start gap-2', className)}>
      <input
        id={inputId}
        type="checkbox"
        className="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
        {...props}
      />
      {label && (
        <label htmlFor={inputId} className="cursor-pointer text-sm text-slate-700">
          {label}
        </label>
      )}
    </div>
  )
}

interface RadioOption<T extends string> {
  value: T
  label: ReactNode
  description?: string
  disabled?: boolean
}

interface RadioGroupProps<T extends string> {
  name: string
  value: T | null | undefined
  onChange: (value: T) => void
  options: RadioOption<T>[]
  columns?: 1 | 2
}

export function RadioGroup<T extends string>({
  name,
  value,
  onChange,
  options,
  columns = 1,
}: RadioGroupProps<T>) {
  return (
    <div className={cn('grid gap-2', columns === 2 ? 'grid-cols-1 sm:grid-cols-2' : 'grid-cols-1')}>
      {options.map((option) => {
        const checked = value === option.value
        return (
          <label
            key={option.value}
            className={cn(
              'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 transition-colors',
              checked ? 'border-brand-500 bg-brand-50/60 ring-1 ring-brand-500' : 'border-slate-200 bg-white',
              option.disabled && 'cursor-not-allowed opacity-50',
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={checked}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
              className="mt-0.5 h-4 w-4 border-slate-300 text-brand-600 focus:ring-brand-500"
            />
            <span>
              <span className="block text-sm font-medium text-slate-800">{option.label}</span>
              {option.description && (
                <span className="mt-0.5 block text-xs text-slate-500">{option.description}</span>
              )}
            </span>
          </label>
        )
      })}
    </div>
  )
}
