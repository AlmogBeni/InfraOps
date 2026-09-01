import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { WizardProvider, useWizard } from '@/features/vm-provisioning/context'
import { initialWizardData, validateStep, type WizardData } from '@/features/vm-provisioning/schema'
import { StorageStep } from '@/features/vm-provisioning/steps/StorageStep'
import { api } from '@/lib/api'

const STORAGE_KEY = 'infraops.provisioning-draft.v2'

function Harness() {
  const wizard = useWizard()
  return (
    <>
      <StorageStep />
      <output data-testid="wizard-state">{JSON.stringify(wizard.data)}</output>
    </>
  )
}

function renderStep() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <WizardProvider><Harness /></WizardProvider>
    </QueryClientProvider>,
  )
}

describe('StorageStep accessible inventory', () => {
  beforeEach(() => {
    localStorage.clear()
    window.history.replaceState({}, '', '/')
    vi.restoreAllMocks()
    vi.spyOn(api, 'datastoreClusters').mockResolvedValue([])
  })

  it('blocks automatic placement and clears stale targets when no datastore is accessible', async () => {
    vi.spyOn(api, 'datastores').mockResolvedValue([{
      id: 'datastore-inaccessible',
      name: 'Offline datastore',
      type: 'VMFS',
      capacity_gb: 500,
      free_gb: 400,
      usage_percent: 20,
      accessible: false,
      datastore_cluster_id: null,
    }])

    const draft = initialWizardData()
    Object.assign(draft, {
      source_type: 'blank',
      vcenter_id: 'vcenter-1',
      datacenter_id: 'datacenter-1',
      cluster_id: 'cluster-1',
      storage_mode: 'auto',
      datastore_id: 'datastore-stale',
      disks: [{ size_gb: 100, provisioning: 'thin', datastore_id: 'datastore-stale' }],
    })
    localStorage.setItem(STORAGE_KEY, JSON.stringify(draft))

    renderStep()

    expect(await screen.findByText('No accessible datastores are available in the selected cluster.')).toBeInTheDocument()
    await waitFor(() => {
      const state = JSON.parse(screen.getByTestId('wizard-state').textContent ?? '{}') as WizardData
      expect(state.storage_mode).toBe('manual')
      expect(state.datastore_id).toBeNull()
      expect(state.disks[0].datastore_id).toBeNull()
      expect(validateStep('configuration', state).datastore_id).toBe('Select a datastore.')
    })

    expect(screen.getByRole('radio', { name: /Automatic placement/i })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /Specific datastore/i })).toBeDisabled()
  })
})
