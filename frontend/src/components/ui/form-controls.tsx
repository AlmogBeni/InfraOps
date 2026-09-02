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
  'w-full rounded-xl border border-[#cbd4cc] bg-gradient-to-b from-white to-[#fbfcf9] px-3 py-2 text-sm text-[#202923] ' +
  'shadow-[0_1px_2px_rgba(23,32,28,0.04),inset_0_1px_0_rgba(255,255,255,0.9)] outline-none ' +
  'transition-[border-color,box-shadow,background-color] duration-200 placeholder:text-[#98a19b] ' +
  'hover:border-[#aebbb1] focus:border-brand-500 focus:bg-white focus:ring-4 focus:ring-brand-100/70 ' +
  'disabled:cursor-not-allowed disabled:border-[#dce2dc] disabled:bg-[#eef1ed] disabled:text-[#7b857f]'

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
    <div className={cn('group/field mb-4', className)}>
      <label className="field-label transition-colors duration-200 group-focus-within/field:text-brand-700" htmlFor={htmlFor}>
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

export function FormSection({
  title,
  description,
  icon,
  children,
  className,
}: {
  title: string
  description?: string
  icon?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn('overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[0_1px_2px_rgba(23,32,28,0.04)]', className)}>
      <div className="flex items-start gap-3 border-b border-[#e1e6e1] bg-gradient-to-r from-[#fafbf8] to-[#f1f6f1] px-5 py-4">
        {icon && <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-700">{icon}</span>}
        <div>
          <h3 className="text-sm font-semibold tracking-[-0.01em] text-[#202923]">{title}</h3>
          {description && <p className="mt-1 text-xs leading-5 text-[#6b756f]">{description}</p>}
        </div>
      </div>
      <div className="p-5 [&>*:last-child]:mb-0">{children}</div>
    </section>
  )
}

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: ReactNode
}

export function Checkbox({ label, className, id, ...props }: CheckboxProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  return (
    <div className={cn('group/check flex items-start gap-2.5', className)}>
      <input
        id={inputId}
        type="checkbox"
        className="mt-0.5 h-4 w-4 rounded border-[#b8c4ba] bg-white text-brand-600 transition-shadow focus:ring-4 focus:ring-brand-100"
        {...props}
      />
      {label && (
        <label htmlFor={inputId} className="cursor-pointer text-sm leading-5 text-[#3f4a43] transition-colors group-hover/check:text-brand-800">
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
              'group/radio flex min-h-20 cursor-pointer items-start gap-3 rounded-2xl border border-[#d8ddd7] bg-gradient-to-br from-white to-[#fafbf8] px-4 py-3.5 transition-[transform,border-color,background-color,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-[#aebbb1] hover:shadow-[0_10px_24px_rgba(23,79,64,0.08)]',
              checked ? 'border-brand-600 bg-brand-50/70 ring-1 ring-brand-500 shadow-[0_10px_24px_rgba(23,79,64,0.1)]' : '',
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
              className="mt-0.5 h-4 w-4 border-[#b8c4ba] bg-white text-brand-600 focus:ring-4 focus:ring-brand-100"
            />
            <span>
              <span className="block text-sm font-semibold text-[#253029] transition-colors group-hover/radio:text-brand-800">{option.label}</span>
              {option.description && (
                <span className="mt-1 block text-xs leading-5 text-[#758079]">{option.description}</span>
              )}
            </span>
          </label>
        )
      })}
    </div>
  )
}
