import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { api } from '@/lib/api'

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ hasPermission: () => true, hasRole: () => true }),
}))

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('DashboardPage', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows a compact operational summary without promotional dashboard copy', async () => {
    vi.spyOn(api, 'dashboard').mockResolvedValue({
      stats: {
        vms_provisioned_this_month: 24,
        success_rate_percent: 96,
        average_duration_seconds: 780,
        failed_jobs: 1,
        active_jobs: 3,
      },
      recent_jobs: [],
      health: [
        { component: 'vCenter production', status: 'connected', detail: '' },
        { component: 'application repository', status: 'healthy', detail: '' },
      ],
    })

    renderPage()

    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'All monitored services operational' })).toBeInTheDocument()
    expect(screen.getByText('Active deployments')).toBeInTheDocument()
    expect(screen.getByText('VMs provisioned')).toBeInTheDocument()
    expect(screen.getByText('Success rate')).toBeInTheDocument()
    expect(screen.queryByText('Estate readiness')).not.toBeInTheDocument()
    expect(screen.queryByText('Monthly performance')).not.toBeInTheDocument()
    expect(screen.queryByText('The control plane is ready')).not.toBeInTheDocument()
  })
})
