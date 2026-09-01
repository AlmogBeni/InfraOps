import { forwardRef, type ButtonHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'ghost-inverse'
export type ButtonSize = 'sm' | 'md' | 'icon'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'border border-[#153f34] bg-[#174f40] text-white shadow-[0_8px_20px_rgba(23,79,64,0.18)] hover:border-[#0f342a] hover:bg-[#123f34]',
  secondary:
    'border border-[#cfd5cf] bg-white text-[#27302c] shadow-sm hover:border-[#aeb9b1] hover:bg-[#f7f8f4]',
  danger:
    'border border-[#a23824] bg-[#b8442e] text-white shadow-sm hover:bg-[#963923]',
  ghost:
    'border border-transparent text-[#65706a] hover:bg-[#e9ede7] hover:text-[#1b2420]',
  'ghost-inverse':
    'border border-transparent text-white/60 hover:bg-white/10 hover:text-white',
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-10 px-4 text-sm',
  icon: 'h-9 w-9 p-0 text-sm',
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
        'inline-flex items-center justify-center gap-1.5 rounded-xl border font-semibold transition-[transform,border-color,background-color,box-shadow,color] duration-200',
        'hover:-translate-y-0.5 active:translate-y-0 active:shadow-sm',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2b7a65] focus-visible:ring-offset-2 focus-visible:ring-offset-white',
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
