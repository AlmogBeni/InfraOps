import { cn } from '@/lib/utils'

/** Monospace output panel used for stage logs and technical details. */

export function LogViewer({
  text,
  className,
  ariaLabel,
}: {
  text: string | null | undefined
  className?: string
  ariaLabel?: string
}) {
  if (!text) return null
  return (
    <pre
      aria-label={ariaLabel}
      className={cn(
        'max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-900 px-3 py-2',
        'font-mono text-xs leading-relaxed text-slate-100',
        className,
      )}
    >
      {text}
    </pre>
  )
}
