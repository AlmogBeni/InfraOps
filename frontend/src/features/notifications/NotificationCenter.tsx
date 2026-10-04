/** Header bell: the signed-in engineer's deployment notifications. */

import { formatDistanceToNowStrict } from 'date-fns'
import { Bell, CheckCircle2, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { desktopAlertState } from '@/features/notifications/announce'
import { useNotifications } from '@/features/notifications/useNotifications'
import { cn } from '@/lib/utils'
import type { NotificationKind, NotificationOut } from '@/types/api'

export const KIND_META: Record<NotificationKind, { icon: typeof Bell; classes: string; label: string }> = {
  JOB_COMPLETED: { icon: CheckCircle2, classes: 'text-emerald-600', label: 'Completed' },
  JOB_FAILED: { icon: XCircle, classes: 'text-red-600', label: 'Failed' },
}

export function relativeTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : `${formatDistanceToNowStrict(date)} ago`
}

export function useOpenNotification() {
  const navigate = useNavigate()
  const { markRead } = useNotifications()
  return (item: NotificationOut) => {
    if (item.read_at === null) markRead(item.id)
    if (item.job_id) navigate(`/jobs/${item.job_id}`)
  }
}

export function NotificationCenter() {
  const { items, unreadCount, markAllRead } = useNotifications()
  const openNotification = useOpenNotification()
  const [open, setOpen] = useState(false)
  const [alerts, setAlerts] = useState(desktopAlertState)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  async function enableDesktopAlerts() {
    try {
      await Notification.requestPermission()
    } finally {
      setAlerts(desktopAlertState())
    }
  }

  const label = unreadCount > 0 ? `Notifications, ${unreadCount} unread` : 'Notifications'

  return (
    <div ref={containerRef} className="relative">
      <Button
        variant="ghost-inverse"
        size="icon"
        aria-label={label}
        aria-expanded={open}
        title={label}
        onClick={() => setOpen((value) => !value)}
        className="relative"
      >
        <Bell className="h-4 w-4" aria-hidden />
        {unreadCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-[#d8f06a] px-1 text-[9px] font-bold text-[#17201c]">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </Button>

      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          className="fixed inset-x-4 top-[4.75rem] overflow-hidden rounded-xl border border-[#d8ddd7] bg-white text-[#243029] shadow-[0_18px_50px_rgba(20,28,24,0.22)] sm:absolute sm:inset-x-auto sm:right-0 sm:top-11 sm:w-96"
        >
          <div className="flex items-center justify-between border-b border-[#e3e7e2] px-4 py-3">
            <p className="text-xs font-bold">Deployment notifications</p>
            <Button variant="ghost" size="sm" disabled={unreadCount === 0} onClick={markAllRead}>
              Mark all as read
            </Button>
          </div>

          {items.length === 0 ? (
            <p className="px-4 py-8 text-center text-xs text-[#68736d]">
              You will be notified here when a deployment you started finishes.
            </p>
          ) : (
            <ul className="max-h-[26rem] divide-y divide-[#eef1ed] overflow-y-auto">
              {items.map((item) => {
                const meta = KIND_META[item.kind] ?? KIND_META.JOB_COMPLETED
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setOpen(false)
                        openNotification(item)
                      }}
                      className={cn(
                        'flex w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-[#f3f5f1]',
                        item.read_at === null && 'bg-brand-50/40',
                      )}
                    >
                      <meta.icon className={cn('mt-0.5 h-4 w-4 shrink-0', meta.classes)} aria-label={meta.label} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-xs font-semibold text-[#1b2420]">{item.title}</span>
                          {item.read_at === null && (
                            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#174f40]" aria-label="Unread" />
                          )}
                        </span>
                        <span className="mt-0.5 line-clamp-2 block text-[11px] leading-4 text-[#5b665f]">{item.message}</span>
                        <span className="mt-1 block text-[10px] text-[#8a938d]">{relativeTime(item.created_at)}</span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}

          <div className="border-t border-[#e3e7e2] bg-[#f7f8f4] px-4 py-2.5 text-[10px] text-[#68736d]">
            {alerts === 'default' && (
              <button type="button" className="font-semibold text-[#174f40] hover:underline" onClick={enableDesktopAlerts}>
                Also show desktop pop-ups
              </button>
            )}
            {alerts === 'granted' && 'Desktop pop-ups are on.'}
            {alerts === 'denied' && 'Desktop pop-ups are blocked in this browser’s site settings.'}
            {alerts === 'insecure' && 'Desktop pop-ups need InfraOps to be opened over HTTPS.'}
            {alerts === 'unsupported' && 'This browser does not support desktop pop-ups.'}
          </div>
        </div>
      )}
    </div>
  )
}
