import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ANNOUNCED_KEY,
  desktopAlertState,
  readAnnounced,
  rememberAnnounced,
  unannounced,
} from '@/features/notifications/announce'
import { NotificationCenter } from '@/features/notifications/NotificationCenter'
import { NotificationToasts } from '@/features/notifications/NotificationToasts'
import { api } from '@/lib/api'
import type { NotificationOut } from '@/types/api'

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u-1', username: 'engineer' } }),
}))

function notification(overrides: Partial<NotificationOut> = {}): NotificationOut {
  return {
    id: 'n-1',
    kind: 'JOB_COMPLETED',
    title: 'TEST3 is ready',
    message: 'Deployment finished and was verified: test3.corp.example · 10.20.30.45.',
    job_id: 'job-1',
    created_at: '2026-09-30T10:00:00Z',
    read_at: null,
    ...overrides,
  }
}

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <NotificationCenter />
        <NotificationToasts />
        <Routes>
          <Route path="/" element={<p>Overview</p>} />
          <Route path="/jobs/:jobId" element={<p>Job page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('notification announcement rules', () => {
  beforeEach(() => localStorage.clear())

  it('announces only unread notifications this browser has not shown, newest first', () => {
    const items = [
      notification({ id: 'old', created_at: '2026-09-30T09:00:00Z' }),
      notification({ id: 'new', created_at: '2026-09-30T11:00:00Z' }),
      notification({ id: 'read', read_at: '2026-09-30T11:05:00Z' }),
      notification({ id: 'seen' }),
    ]
    rememberAnnounced(['seen'])

    expect(unannounced(items, readAnnounced()).map((item) => item.id)).toEqual(['new', 'old'])
  })

  it('keeps a bounded memory of announced notifications', () => {
    rememberAnnounced(Array.from({ length: 250 }, (_, index) => `id-${index}`))

    const remembered = JSON.parse(localStorage.getItem(ANNOUNCED_KEY) ?? '[]') as string[]
    expect(remembered).toHaveLength(200)
    expect(remembered.at(-1)).toBe('id-249')
  })

  it('reports that desktop pop-ups need HTTPS on a plain-HTTP page', () => {
    vi.stubGlobal('Notification', { permission: 'default', requestPermission: vi.fn() })
    const original = window.isSecureContext
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    try {
      expect(desktopAlertState()).toBe('insecure')
    } finally {
      Object.defineProperty(window, 'isSecureContext', { value: original, configurable: true })
      vi.unstubAllGlobals()
    }
  })
})

describe('in-app notifications', () => {
  beforeEach(() => {
    localStorage.clear()
    document.title = 'InfraOps'
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('pops up a finished deployment once, counts it in the bell and the tab title', async () => {
    vi.spyOn(api, 'notifications').mockResolvedValue({ items: [notification()], unread_count: 1 })

    renderApp()

    const popups = screen.getByRole('region', { name: 'New notifications' })
    expect(await within(popups).findByText('TEST3 is ready')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Notifications, 1 unread' })).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('(1) InfraOps'))
    expect(readAnnounced().has('n-1')).toBe(true)
  })

  it('does not pop up again what this browser already announced', async () => {
    rememberAnnounced(['n-1'])
    vi.spyOn(api, 'notifications').mockResolvedValue({ items: [notification()], unread_count: 1 })

    renderApp()

    await screen.findByRole('button', { name: 'Notifications, 1 unread' })
    expect(screen.queryByText('Deployment finished and was verified', { exact: false })).not.toBeInTheDocument()
  })

  it('opens the deployment from the pop-up and marks the notification read', async () => {
    vi.spyOn(api, 'notifications').mockResolvedValue({ items: [notification()], unread_count: 1 })
    const markRead = vi.spyOn(api, 'markNotificationRead').mockResolvedValue(undefined)

    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'View deployment' }))

    expect(await screen.findByText('Job page')).toBeInTheDocument()
    expect(markRead).toHaveBeenCalledWith('n-1')
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'View deployment' })).not.toBeInTheDocument(),
    )
  })

  it('lists notifications in the bell and marks them all read', async () => {
    rememberAnnounced(['n-1', 'n-2'])
    const failed = notification({
      id: 'n-2', kind: 'JOB_FAILED', title: 'APP7 deployment failed', message: "'Join domain' failed.",
    })
    const listing = vi.spyOn(api, 'notifications').mockResolvedValue({
      items: [notification(), failed],
      unread_count: 2,
    })
    const markAll = vi.spyOn(api, 'markAllNotificationsRead').mockImplementation(async () => {
      const readAt = '2026-09-30T12:00:00Z'
      listing.mockResolvedValue({
        items: [notification({ read_at: readAt }), { ...failed, read_at: readAt }],
        unread_count: 0,
      })
    })

    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }))

    const panel = screen.getByRole('dialog', { name: 'Notifications' })
    expect(panel).toHaveTextContent('TEST3 is ready')
    expect(panel).toHaveTextContent('APP7 deployment failed')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Mark all as read' }))
    })
    expect(markAll).toHaveBeenCalled()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument())
  })
})
