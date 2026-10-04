import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { JobDetailPage } from '@/features/jobs/JobDetailPage'
import { api } from '@/lib/api'
import type { JobDetailOut, JobStepOut } from '@/types/api'

const permissions = vi.hoisted(() => ({ current: [] as string[] }))

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u-1', username: 'engineer', permissions: permissions.current } }),
}))

function step(overrides: Partial<JobStepOut> = {}): JobStepOut {
  return {
    id: 'step-1',
    job_id: 'job-1',
    stage_key: 'wait_for_guest_os',
    name: 'Install Windows',
    sequence: 8,
    status: 'FAILED',
    attempt: 1,
    max_attempts: 3,
    retryable: true,
    started_at: '2026-10-04T08:00:00Z',
    finished_at: '2026-10-04T10:00:00Z',
    output: null,
    error_human: 'Windows did not finish installing from the ISO in time.',
    error_technical: 'waited 7140s\n\nConsole screenshot at failure: [SAN-01] SERVER-01/SERVER-01-1.png',
    artifacts: { console_screenshot: '[SAN-01] SERVER-01/SERVER-01-1.png' },
    ...overrides,
  }
}

function job(overrides: Partial<JobDetailOut> = {}): JobDetailOut {
  return {
    id: 'job-1',
    job_type: 'vm_provisioning',
    status: 'PARTIALLY_COMPLETED',
    vm_name: 'SERVER-01',
    datacenter_id: 'dc-1',
    datacenter_name: 'DC01',
    requested_by_username: 'engineer',
    current_stage: 'wait_for_guest_os',
    progress: 40,
    infrastructure_status: 'READY',
    guest_os_status: 'ERROR',
    vmware_tools_status: 'NOT_APPLICABLE_YET',
    guest_provisioning_status: 'FAILED',
    error_summary: 'Windows did not finish installing from the ISO in time.',
    cancel_requested: false,
    queued_at: '2026-10-04T07:59:00Z',
    started_at: '2026-10-04T08:00:00Z',
    finished_at: '2026-10-04T10:00:00Z',
    duration_seconds: 7200,
    steps: [step()],
    request_payload: null,
    legacy_request: false,
    ...overrides,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/jobs/job-1']}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('JobDetailPage', () => {
  beforeEach(() => {
    permissions.current = ['jobs.retry', 'jobs.cancel']
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows a job of a removed workflow read-only and offers no retry', async () => {
    vi.spyOn(api, 'job').mockResolvedValue(job({
      status: 'FAILED',
      legacy_request: true,
      request_payload: { source_type: 'blank', guest: { iso_id: null } },
      steps: [step({ stage_key: 'create_vm', name: 'Create virtual machine', artifacts: {} })],
    }))

    renderPage()

    expect(await screen.findByText('Created by a provisioning workflow that no longer exists')).toBeInTheDocument()
    expect(screen.getByLabelText('Stored request')).toHaveTextContent('"iso_id": null')
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
  })

  it('lets administrators open the console screenshot of a failed installation stage', async () => {
    permissions.current = ['jobs.retry', 'admin.settings']
    vi.spyOn(api, 'job').mockResolvedValue(job())
    const screenshot = vi.spyOn(api, 'consoleScreenshot').mockResolvedValue(new Blob(['png'], { type: 'image/png' }))
    // jsdom has no object URLs.
    const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL }
    URL.createObjectURL = vi.fn(() => 'blob:console')
    URL.revokeObjectURL = vi.fn()
    try {
      renderPage()

      fireEvent.click(await screen.findByText('View technical output'))
      fireEvent.click(screen.getByRole('button', { name: 'Show console screenshot' }))

      const image = await screen.findByRole('img', { name: "VM console when 'Install Windows' failed" })
      expect(image).toHaveAttribute('src', 'blob:console')
      expect(screenshot).toHaveBeenCalledWith('job-1', 'wait_for_guest_os')
      expect(screen.getByRole('button', { name: /Retry stage/ })).toBeInTheDocument()
    } finally {
      cleanup()
      URL.createObjectURL = original.create
      URL.revokeObjectURL = original.revoke
    }
  })

  it('never offers the screenshot to engineers without administrator rights', async () => {
    vi.spyOn(api, 'job').mockResolvedValue(job())
    const screenshot = vi.spyOn(api, 'consoleScreenshot')

    renderPage()

    await screen.findByText('Install Windows')
    expect(screen.queryByRole('button', { name: 'Show console screenshot' })).not.toBeInTheDocument()
    await waitFor(() => expect(screenshot).not.toHaveBeenCalled())
  })
})
