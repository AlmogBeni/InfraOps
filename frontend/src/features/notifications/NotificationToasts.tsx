/** Pops up notifications the moment they arrive, anywhere in the app. */

import { X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  MAX_POPUPS,
  readAnnounced,
  rememberAnnounced,
  showDesktopAlert,
  unannounced,
} from '@/features/notifications/announce'
import { KIND_META, useOpenNotification } from '@/features/notifications/NotificationCenter'
import { useNotifications } from '@/features/notifications/useNotifications'
import { cn } from '@/lib/utils'
import type { NotificationOut } from '@/types/api'

const TITLE_PREFIX = /^\(\d+\+?\)\s+/

export function NotificationToasts() {
  const { items, unreadCount } = useNotifications()
  const openNotification = useOpenNotification()
  const openRef = useRef(openNotification)
  openRef.current = openNotification
  const [toasts, setToasts] = useState<NotificationOut[]>([])

  useEffect(() => {
    const fresh = unannounced(items, readAnnounced())
    if (fresh.length === 0) return
    rememberAnnounced(fresh.map((item) => item.id))
    const shown = fresh.slice(0, MAX_POPUPS)
    setToasts((current) =>
      [...shown.filter((item) => !current.some((toast) => toast.id === item.id)), ...current].slice(0, MAX_POPUPS),
    )
    for (const item of shown) showDesktopAlert(item, () => openRef.current(item))
  }, [items])

  // The tab title shows the unread count, so a background tab is noticed too.
  useEffect(() => {
    const base = document.title.replace(TITLE_PREFIX, '')
    document.title = unreadCount > 0 ? `(${unreadCount > 9 ? '9+' : unreadCount}) ${base}` : base
  }, [unreadCount])

  // A notification opened from the bell or another tab no longer needs a pop-up.
  const visible = toasts.filter(
    (toast) => items.find((item) => item.id === toast.id)?.read_at == null,
  )
  const dismiss = (id: string) => setToasts((current) => current.filter((toast) => toast.id !== id))

  return (
    <div
      role="region"
      aria-label="New notifications"
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-3"
    >
      {visible.map((item) => {
        const meta = KIND_META[item.kind] ?? KIND_META.JOB_COMPLETED
        return (
          <div
            key={item.id}
            className={cn(
              'pointer-events-auto rounded-xl border bg-white p-4 text-[#243029] shadow-[0_18px_50px_rgba(20,28,24,0.22)]',
              item.kind === 'JOB_COMPLETED' && 'border-emerald-200',
              item.kind === 'JOB_FAILED' && 'border-red-200',
            )}
          >
            <div className="flex items-start gap-3">
              <meta.icon className={cn('mt-0.5 h-5 w-5 shrink-0', meta.classes)} aria-label={meta.label} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-[#1b2420]">{item.title}</p>
                <p className="mt-1 text-xs leading-5 text-[#4f5a53]">{item.message}</p>
                {item.job_id && (
                  <Button
                    size="sm"
                    className="mt-3"
                    onClick={() => {
                      dismiss(item.id)
                      openNotification(item)
                    }}
                  >
                    View deployment
                  </Button>
                )}
              </div>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                className="rounded-md p-1 text-[#7b857f] hover:bg-[#eef1ed] hover:text-[#1b2420]"
                aria-label={`Dismiss ${item.title}`}
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
