import { X } from 'lucide-react'
import { useEffect, useId, useRef, type ReactNode } from 'react'

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
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    const focusableSelector =
      'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    const getFocusable = () => panelRef.current?.querySelectorAll<HTMLElement>(focusableSelector)
    const initialFocus = panel?.querySelector<HTMLElement>('[autofocus]')
      ?? panel?.querySelector<HTMLElement>(
        'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]):not([data-dialog-close]), a[href], [tabindex]:not([tabindex="-1"])',
      )
      ?? panel?.querySelector<HTMLElement>(focusableSelector)
    initialFocus?.focus()

    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onCloseRef.current()
        return
      }
      const focusable = getFocusable()
      if (event.key !== 'Tab' || !focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
      previous?.focus()
    }
  }, [open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button type="button" aria-label="Close dialog" className="absolute inset-0 cursor-default bg-[#111714]/70 backdrop-blur-sm" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={cn(
          'relative z-10 flex max-h-[88vh] w-full animate-panel-reveal flex-col overflow-hidden rounded-3xl border border-white/30 bg-white shadow-[0_28px_80px_rgba(12,20,16,0.3)]',
          wide ? 'max-w-3xl' : 'max-w-lg',
        )}
      >
        <header className="flex items-center justify-between border-b border-[#dce4dd] bg-[linear-gradient(135deg,#fbfcf9_0%,#eef5ef_100%)] px-5 py-4">
          <h2 id={titleId} className="text-base font-semibold tracking-[-0.02em] text-[#1b2420]">{title}</h2>
          <button type="button" data-dialog-close onClick={onClose} aria-label="Close dialog" className="rounded-lg p-1.5 text-[#758079] transition-colors hover:bg-[#e9ede7] hover:text-[#202923]">
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-5">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-[#dce4dd] bg-[linear-gradient(135deg,#f8faf6_0%,#eef4ef_100%)] px-5 py-4">{footer}</footer>}
      </div>
    </div>
  )
}
