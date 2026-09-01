import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto rounded-xl border border-[#d6def0] bg-white/95 shadow-[0_12px_30px_rgba(15,23,42,0.08)]">
      <table className={cn('table-base', className)} {...props} />
    </div>
  )
}

export function Th({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn('whitespace-nowrap', className)} {...props} />
}

export function Td({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={className} {...props} />
}

export function Tr({
  clickable,
  className,
  ...props
}: HTMLAttributes<HTMLTableRowElement> & { clickable?: boolean }) {
  return (
    <tr
      className={cn(
        'animate-row-enter',
        clickable && 'cursor-pointer transition-all duration-150 hover:bg-brand-50/80 hover:shadow-[0_2px_12px_rgba(37,99,235,0.15)]',
        className,
      )}
      {...props}
    />
  )
}
