import { X } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
}) {
  useEffect(() => {
    if (!open) return
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        aria-hidden
        className="absolute inset-0 bg-slate-950/65 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          'relative z-10 flex max-h-[88vh] w-full animate-panel-reveal flex-col rounded-xl border border-[#d6def0] bg-white/95 shadow-[0_20px_45px_rgba(12,25,51,0.32)]',
          wide ? 'max-w-2xl' : 'max-w-lg',
        )}
      >
        <header className="flex items-center justify-between border-b border-[#d6def0] bg-gradient-to-r from-[#091428] to-[#091b37] px-4 py-3">
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded-md border border-transparent bg-white/10 p-1 text-slate-200 transition-colors hover:border-white/25 hover:bg-white/20 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="overflow-y-auto px-4 py-3">{children}</div>
        {footer && (
          <footer className="flex justify-end gap-2 border-t border-[#d6def0] bg-[#f8faff] px-4 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}
