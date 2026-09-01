import { forwardRef, type ButtonHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'
export type ButtonSize = 'sm' | 'md'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'border border-transparent bg-gradient-to-r from-brand-600 to-brand-700 text-white shadow-[0_10px_24px_rgba(33,87,184,0.25)] transition-[transform,box-shadow,background-color] active:translate-y-[0.5px] hover:-translate-y-[1px] hover:from-brand-700 hover:to-brand-800 hover:shadow-[0_14px_30px_rgba(33,87,184,0.3)]',
  secondary:
    'border border-[#d6def0] bg-white/85 text-slate-800 hover:border-brand-200 hover:bg-[#f5f8ff] active:translate-y-[0.5px]',
  danger:
    'border border-transparent bg-gradient-to-r from-red-600 to-red-700 text-white shadow-[0_10px_24px_rgba(220,38,38,0.28)] transition-[transform,box-shadow,background-color] active:translate-y-[0.5px] hover:-translate-y-[1px] hover:from-red-700 hover:to-red-800 hover:shadow-[0_14px_30px_rgba(220,38,38,0.33)]',
  ghost:
    'border border-transparent text-slate-600 hover:border-[#d6def0] hover:bg-[#f7faff] hover:text-slate-950 active:translate-y-[0.5px]',
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-9 px-4 text-sm',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'primary', size = 'md', loading, disabled, children, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-md border font-semibold transition-all duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white',
        'disabled:pointer-events-none disabled:opacity-50',
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      {...props}
    >
      {loading && (
        <span
          aria-hidden
          className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  )
})
