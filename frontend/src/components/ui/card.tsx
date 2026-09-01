import type { HTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'shell-card rounded-2xl shadow-[var(--ui-shadow)] transition-[border-color,box-shadow] duration-300 hover:border-[#c5d0c7] hover:shadow-[0_2px_4px_rgba(23,32,28,0.04),0_18px_42px_rgba(23,79,64,0.08)]',
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
        'border-b border-[#dce4dd] bg-[linear-gradient(135deg,#fbfcf9_0%,#f0f6f0_100%)] px-5 py-4',
        className,
      )}
      {...props}
    />
  )
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn('text-sm font-semibold tracking-[-0.01em] text-[#202923]', className)} {...props} />
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-5 py-4', className)} {...props} />
}

export function CardFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('border-t border-[#dce4dd] bg-[linear-gradient(135deg,#f8faf6_0%,#eef4ef_100%)] px-5 py-4', className)} {...props} />
  )
}
