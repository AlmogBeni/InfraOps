import type { HTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'shell-card rounded-xl shadow-[var(--ui-shadow)]',
        className,
      )}
      {...props}
    />
  )
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'border-b border-[#d6def0] bg-gradient-to-r from-[#f8faff] to-[#eef3ff] px-4 py-2.5',
        className,
      )}
      {...props}
    />
  )
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn('text-xs font-bold uppercase tracking-[0.08em] text-slate-700', className)} {...props} />
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-4 py-3', className)} {...props} />
}

export function CardFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('border-t border-[#d6def0] bg-[#f8fbff] px-4 py-3', className)} {...props} />
  )
}
