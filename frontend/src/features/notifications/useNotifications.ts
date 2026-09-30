/** The signed-in engineer's notifications, kept fresh by polling. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { NotificationListResponse } from '@/types/api'

export const NOTIFICATIONS_QUERY_KEY = ['notifications'] as const
const POLL_MS = 20_000

export function useNotifications() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: NOTIFICATIONS_QUERY_KEY,
    queryFn: () => api.notifications(),
    enabled: Boolean(user),
    refetchInterval: POLL_MS,
    // Keep polling in a background tab so a finished deployment is noticed.
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
    staleTime: 5_000,
  })

  async function markLocallyRead(ids: string[] | 'all') {
    // A poll already in flight must not overwrite the optimistic update.
    await queryClient.cancelQueries({ queryKey: NOTIFICATIONS_QUERY_KEY })
    const now = new Date().toISOString()
    queryClient.setQueryData<NotificationListResponse>(NOTIFICATIONS_QUERY_KEY, (current) => {
      if (!current) return current
      const items = current.items.map((item) =>
        item.read_at === null && (ids === 'all' || ids.includes(item.id)) ? { ...item, read_at: now } : item,
      )
      const newlyRead = current.items.filter(
        (item) => item.read_at === null && (ids === 'all' || ids.includes(item.id)),
      ).length
      return { items, unread_count: Math.max(0, current.unread_count - newlyRead) }
    })
  }

  const refresh = () => queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY })
  const markRead = useMutation({
    mutationFn: (id: string) => api.markNotificationRead(id),
    onMutate: (id) => markLocallyRead([id]),
    onSettled: refresh,
  })
  const markAllRead = useMutation({
    mutationFn: () => api.markAllNotificationsRead(),
    onMutate: () => markLocallyRead('all'),
    onSettled: refresh,
  })

  return {
    items: query.data?.items ?? [],
    unreadCount: query.data?.unread_count ?? 0,
    loading: query.isLoading,
    markRead: (id: string) => markRead.mutate(id),
    markAllRead: () => markAllRead.mutate(),
  }
}
