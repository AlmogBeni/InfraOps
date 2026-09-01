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
  'w-full rounded-lg border border-[#cfd5cf] bg-white px-3 py-2 text-sm text-[#202923] ' +
  'shadow-sm outline-none transition-[border,box-shadow] duration-150 placeholder:text-[#9aa39d] ' +
  'focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:cursor-not-allowed disabled:border-[#dde2dd] disabled:bg-[#eef1ed] disabled:text-[#7b857f]'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(baseControl, 'h-10', className)} {...props} />
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
      <select ref={ref} className={cn(baseControl, 'h-10 pr-8', className)} {...props}>
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
      {hint && !error && <p className="mt-1.5 text-[11px] leading-4 text-[#758079]">{hint}</p>}
      {error && (
        <p className="mt-1.5 text-[11px] font-medium text-red-700" role="alert">
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
        className="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white text-brand-600 focus:ring-2 focus:ring-brand-200"
        {...props}
      />
      {label && (
        <label htmlFor={inputId} className="cursor-pointer text-sm leading-5 text-[#3f4a43]">
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
              'flex min-h-20 cursor-pointer items-start gap-3 rounded-xl border border-[#d8ddd7] bg-white px-4 py-3 transition-[border,background-color,box-shadow] hover:bg-[#f8f9f6]',
              checked ? 'border-brand-600 bg-brand-50/60 ring-1 ring-brand-500 shadow-sm' : '',
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
              className="mt-0.5 h-4 w-4 border-slate-300 bg-white text-brand-600 focus:ring-2 focus:ring-brand-200"
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
