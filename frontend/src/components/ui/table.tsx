import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)] transition-[border-color,box-shadow] duration-300 hover:border-[#c5d0c7] hover:shadow-[0_2px_4px_rgba(23,32,28,0.04),0_18px_42px_rgba(23,79,64,0.08)]">
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
        clickable && 'cursor-pointer transition-colors duration-200 hover:bg-[#f3f8f3]',
        className,
      )}
      {...props}
    />
  )
}
