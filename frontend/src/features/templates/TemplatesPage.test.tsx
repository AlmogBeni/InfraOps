import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TemplatesPage } from '@/features/templates/TemplatesPage'
import { api } from '@/lib/api'
import type { DatacenterOut, TemplateOut, VCenterSummary } from '@/types/api'

const VCENTERS: VCenterSummary[] = [
  {
    id: 'vc-primary',
    name: 'Primary vCenter',
    host: 'vcenter.example.test',
    port: 443,
    enabled: true,
    connection_state: 'connected',
    last_checked_at: '2026-08-31T10:00:00Z',
  },
]

const DATACENTERS: DatacenterOut[] = [
  { id: 'dc-production', name: 'Production Datacenter', vcenter_id: 'vc-primary' },
]

const PACKAGES: TemplateOut[] = [
  {
    id: 'pkg-windows-ovf',
    name: 'Windows Application Appliance',
    type: 'OVF',
    description: 'Production application server package.',
    datacenter_id: 'dc-production',
    datacenter_name: 'Production Datacenter',
    storage_name: 'Production Library',
    location: 'Production Library / Windows',
    size_bytes: 4_294_967_296,
    last_modified: '2026-08-30T08:00:00Z',
  },
  {
    id: 'pkg-linux-ova',
    name: 'Linux Operations Appliance',
    type: 'OVA',
    description: 'Operations utility package.',
    datacenter_id: 'dc-production',
    datacenter_name: 'Production Datacenter',
    storage_name: 'Production Library',
    location: 'Production Library / Linux',
    size_bytes: 2_147_483_648,
    last_modified: '2026-08-29T08:00:00Z',
  },
]

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <TemplatesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function mockTargetInventory(loadPackages: () => Promise<TemplateOut[]>) {
  const vcenters = vi.spyOn(api, 'vcenters').mockResolvedValue(VCENTERS)
  const datacenters = vi.spyOn(api, 'datacenters').mockResolvedValue(DATACENTERS)
  const templates = vi.spyOn(api, 'templates').mockImplementation(loadPackages)
  return { vcenters, datacenters, templates }
}

async function chooseProductionTarget() {
  await screen.findByRole('option', { name: 'Primary vCenter' })
  fireEvent.change(screen.getByLabelText('vCenter'), { target: { value: 'vc-primary' } })
  await screen.findByRole('option', { name: 'Production Datacenter' })
  fireEvent.change(screen.getByLabelText('Target datacenter'), {
    target: { value: 'dc-production' },
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('TemplatesPage datacenter-scoped package inventory', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('requires both target selectors before requesting and rendering packages', async () => {
    const mocks = mockTargetInventory(() => Promise.resolve(PACKAGES))

    renderPage()

    const datacenterSelect = screen.getByLabelText('Target datacenter')
    expect(datacenterSelect).toBeDisabled()
    expect(mocks.datacenters).not.toHaveBeenCalled()
    expect(mocks.templates).not.toHaveBeenCalled()

    await screen.findByRole('option', { name: 'Primary vCenter' })
    expect(screen.getByText('Choose a deployment target to browse packages')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('vCenter'), { target: { value: 'vc-primary' } })

    await waitFor(() => expect(mocks.datacenters).toHaveBeenCalledWith('vc-primary'))
    expect(mocks.templates).not.toHaveBeenCalled()

    await screen.findByRole('option', { name: 'Production Datacenter' })
    fireEvent.change(datacenterSelect, { target: { value: 'dc-production' } })

    await waitFor(() => {
      expect(mocks.templates).toHaveBeenCalledTimes(1)
      expect(mocks.templates).toHaveBeenCalledWith('vc-primary', 'dc-production')
    })
    expect(await screen.findByRole('heading', { name: 'Windows Application Appliance' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Linux Operations Appliance' })).toBeInTheDocument()
    expect(screen.getByText('OVF')).toBeInTheDocument()
    expect(screen.getByText('OVA')).toBeInTheDocument()
  })

  it('presents intentional package loading and empty states for the chosen target', async () => {
    const request = deferred<TemplateOut[]>()
    mockTargetInventory(() => request.promise)
    renderPage()

    await chooseProductionTarget()
    expect(await screen.findByText('Loading OVF and OVA packages')).toBeInTheDocument()

    await act(async () => request.resolve([]))

    expect(
      await screen.findByText('No OVF or OVA packages are available for this target'),
    ).toBeInTheDocument()
  })

  it('presents a recoverable error when scoped package discovery fails', async () => {
    mockTargetInventory(() => Promise.reject(new Error('Package inventory is temporarily unavailable.')))
    renderPage()

    await chooseProductionTarget()

    expect(await screen.findByText('Package inventory could not be loaded')).toBeInTheDocument()
    expect(screen.getByText('Package inventory is temporarily unavailable.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Try again/i })).toBeEnabled()
  })
})
