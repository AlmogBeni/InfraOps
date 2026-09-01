import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { WizardProvider } from '@/features/vm-provisioning/context'
import { initialWizardData } from '@/features/vm-provisioning/schema'
import { InfrastructureStep } from '@/features/vm-provisioning/steps/InfrastructureStep'
import { api } from '@/lib/api'

function renderStep() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <WizardProvider><InfrastructureStep /></WizardProvider>
    </QueryClientProvider>,
  )
}

describe('InfrastructureStep cluster-to-host flow', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
    const draft = initialWizardData()
    Object.assign(draft, {
      source_type: 'blank',
      vcenter_id: 'vc-1',
      site_id: 'site-1',
      datacenter_id: 'dc-1',
      cluster_id: 'cluster-a',
      host_mode: 'manual',
      host_id: 'host-a',
    })
    localStorage.setItem('infraops.provisioning-draft', JSON.stringify(draft))

    vi.spyOn(api, 'vcenters').mockResolvedValue([
      { id: 'vc-1', name: 'vCenter', host: 'vc.local', port: 443, enabled: true, connection_state: 'connected', last_checked_at: null },
    ])
    vi.spyOn(api, 'sites').mockResolvedValue([
      { id: 'site-1', name: 'Primary', description: '', vcenter_id: 'vc-1', enabled: true },
    ])
    vi.spyOn(api, 'datacenters').mockResolvedValue([{ id: 'dc-1', name: 'DC1', vcenter_id: 'vc-1' }])
    vi.spyOn(api, 'clusters').mockResolvedValue([
      { id: 'cluster-a', name: 'Cluster A', datacenter_id: 'dc-1', drs_enabled: true, hosts_count: 1, total_cpu_cores: 32, total_memory_gb: 256 },
      { id: 'cluster-b', name: 'Cluster B', datacenter_id: 'dc-1', drs_enabled: true, hosts_count: 1, total_cpu_cores: 32, total_memory_gb: 256 },
      { id: 'cluster-empty', name: 'Empty Cluster', datacenter_id: 'dc-1', drs_enabled: true, hosts_count: 0, total_cpu_cores: 0, total_memory_gb: 0 },
      { id: 'cluster-error', name: 'Error Cluster', datacenter_id: 'dc-1', drs_enabled: true, hosts_count: 1, total_cpu_cores: 32, total_memory_gb: 256 },
    ])
    vi.spyOn(api, 'hosts').mockImplementation(async (_vcenterId, clusterId) => {
      if (clusterId === 'cluster-error') throw new Error('vCenter host inventory is unavailable.')
      if (clusterId === 'cluster-empty') return []
      const suffix = clusterId === 'cluster-a' ? 'a' : 'b'
      return [{
        id: `host-${suffix}`,
        name: `esxi-${suffix}.local`,
        connection_state: 'connected',
        maintenance_mode: false,
        cpu_usage_percent: 20,
        memory_usage_percent: 30,
        available_for_provisioning: true,
      }]
    })
    vi.spyOn(api, 'resourcePools').mockResolvedValue([])
    vi.spyOn(api, 'templates').mockResolvedValue([{
      id: 'vm-template-1',
      name: 'Windows Server 2025 Base',
      os_family: 'windows',
      os_version: 'Windows Server 2025',
      last_modified: null,
      description: 'Corporate base image',
      datacenter_id: 'dc-1',
      datacenter_name: 'DC1',
      cpu: 4,
      memory_mb: 16384,
      disk_size_gb: 120,
    }])
  })

  it('loads cluster hosts and clears a stale host when the cluster changes', async () => {
    renderStep()
    const hostSelect = await screen.findByLabelText(/Host/)
    await waitFor(() => expect(hostSelect).toHaveValue('host-a'))
    expect(screen.getByRole('option', { name: 'esxi-a.local' })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/Cluster/), { target: { value: 'cluster-b' } })
    expect(hostSelect).toHaveValue('')
    expect(await screen.findByRole('option', { name: 'esxi-b.local' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'esxi-a.local' })).not.toBeInTheDocument()
    expect(api.hosts).toHaveBeenCalledWith('vc-1', 'cluster-b')
  })

  it('distinguishes empty and failed host retrieval', async () => {
    renderStep()
    await screen.findByRole('option', { name: 'esxi-a.local' })

    fireEvent.change(screen.getByLabelText(/Cluster/), { target: { value: 'cluster-empty' } })
    expect(await screen.findByRole('option', { name: /No hosts are available/i })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/Cluster/), { target: { value: 'cluster-error' } })
    expect(await screen.findByText('vCenter host inventory is unavailable.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry hosts/i })).toBeInTheDocument()
  })

  it('loads datacenter templates and stores the selected provider reference', async () => {
    const draft = JSON.parse(localStorage.getItem('infraops.provisioning-draft') ?? '{}')
    draft.source_type = 'template'
    draft.template_id = ''
    localStorage.setItem('infraops.provisioning-draft', JSON.stringify(draft))

    renderStep()
    const template = await screen.findByRole('radio', { name: /Windows Server 2025 Base/i })
    expect(template).toHaveTextContent('4 CPU')
    expect(template).toHaveTextContent('16 GB RAM')
    expect(api.templates).toHaveBeenCalledWith('vc-1', 'dc-1')

    fireEvent.click(template)
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('infraops.provisioning-draft') ?? '{}')
      expect(stored.template_id).toBe('vm-template-1')
    })
  })
})
