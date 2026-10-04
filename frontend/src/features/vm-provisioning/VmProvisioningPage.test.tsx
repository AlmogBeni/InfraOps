import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { initialWizardData } from '@/features/vm-provisioning/schema'
import { VmProvisioningPage } from '@/features/vm-provisioning/VmProvisioningPage'
import { ApiError, api } from '@/lib/api'
import type { JobOut } from '@/types/api'

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ hasPermission: () => true, hasRole: () => true }),
}))

function mockInventory() {
  vi.spyOn(api, 'vcenters').mockResolvedValue([
    { id: 'vc-1', name: 'vCenter', host: 'vc.example.test', port: 443, enabled: true, connection_state: 'connected', last_checked_at: null },
  ])
  vi.spyOn(api, 'datacenters').mockResolvedValue([{ id: 'dc-1', name: 'DC01', vcenter_id: 'vc-1' }])
  vi.spyOn(api, 'clusters').mockResolvedValue([
    { id: 'cl-1', name: 'PROD', datacenter_id: 'dc-1', drs_enabled: true, hosts_count: 1, total_cpu_cores: 32, total_memory_gb: 256 },
  ])
  vi.spyOn(api, 'hosts').mockResolvedValue([
    { id: 'host-1', name: 'esx01', connection_state: 'connected', maintenance_mode: false, cpu_usage_percent: 10, memory_usage_percent: 20, available_for_provisioning: true },
  ])
  vi.spyOn(api, 'resourcePools').mockResolvedValue([])
  vi.spyOn(api, 'datastores').mockResolvedValue([
    { id: 'ds-1', name: 'SAN-01', type: 'VMFS', capacity_gb: 2000, free_gb: 1500, usage_percent: 25, accessible: true, datastore_cluster_id: null },
  ])
  vi.spyOn(api, 'datastoreClusters').mockResolvedValue([])
  vi.spyOn(api, 'networks').mockResolvedValue([{ id: 'net-1', name: 'VLAN100', type: 'DISTRIBUTED_PORT_GROUP' }])
  vi.spyOn(api, 'isos').mockResolvedValue([
    {
      id: 'iso-1', name: 'Windows Server 2025.iso', datacenter_id: 'dc-1', datacenter_name: 'DC01',
      datastore_id: 'ds-1', datastore_name: 'SAN-01', path: '[SAN-01] iso/ws2025.iso', size_bytes: 1, last_modified: null,
    },
  ])
  vi.spyOn(api, 'provisioningCredentials').mockImplementation(async (purpose) => (
    purpose === 'guest_administrator'
      ? [{ name: 'guest-admin', purpose: 'guest_administrator', revision: 1, updated_at: null }]
      : []
  ))
  vi.spyOn(api, 'certificatePackages').mockResolvedValue([])
  vi.spyOn(api, 'applications').mockResolvedValue([])
}

function saveValidDraft() {
  const draft = initialWizardData()
  Object.assign(draft, {
    vcenter_id: 'vc-1',
    datacenter_id: 'dc-1',
    cluster_id: 'cl-1',
    vm_name: 'SERVER-01',
    iso_id: 'iso-1',
    guest_credential_secret_ref: 'guest-admin',
    network_id: 'net-1',
    ip_mode: 'DHCP',
  })
  localStorage.setItem('infraops.provisioning-draft.v3', JSON.stringify(draft))
}

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/provisioning/new']}>
        <Routes>
          <Route path="/provisioning/new" element={<VmProvisioningPage />} />
          <Route path="/jobs/:jobId" element={<p>Job page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function continueToReview() {
  for (let step = 0; step < 6; step += 1) {
    fireEvent.click(await screen.findByRole('button', { name: /^Continue/ }))
  }
  return screen.findByRole('button', { name: /Create virtual machine/ })
}

describe('VM provisioning submission', () => {
  beforeEach(() => {
    localStorage.clear()
    mockInventory()
    saveValidDraft()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('submits without a confirmation dialog or a separate dry run', async () => {
    const validate = vi.spyOn(api, 'validate')
    const submit = vi.spyOn(api, 'submitJob').mockResolvedValue({ id: 'job-1' } as JobOut)

    renderWizard()
    fireEvent.click(await continueToReview())

    expect(await screen.findByText('Job page')).toBeInTheDocument()
    expect(validate).not.toHaveBeenCalled()
    const [payload] = submit.mock.calls[0]
    expect(payload.source_type).toBe('blank')
    expect(payload.guest.iso_id).toBe('iso-1')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the server preflight blocking checks inline when nothing was created', async () => {
    vi.spyOn(api, 'submitJob').mockRejectedValue(
      new ApiError(422, 'preflight_failed', 'Preflight checks failed.', {
        blocking_checks: [
          {
            code: 'windows_media',
            label: 'Windows Server installation media',
            status: 'FAIL',
            detail: 'Windows 11 24H2.iso is Windows client media.',
            blocking: true,
          },
        ],
      }),
    )

    renderWizard()
    fireEvent.click(await continueToReview())

    expect(await screen.findByText('Deployment is not ready — nothing was created')).toBeInTheDocument()
    const checks = screen.getByRole('list', { name: 'Blocking checks' })
    expect(checks).toHaveTextContent('Windows Server installation media:')
    expect(checks).toHaveTextContent('Windows 11 24H2.iso is Windows client media.')
    await waitFor(() => expect(screen.getByRole('button', { name: /Create virtual machine/ })).toBeEnabled())
  })
})
