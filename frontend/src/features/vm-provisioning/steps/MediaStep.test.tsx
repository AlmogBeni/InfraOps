import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WizardProvider, useWizard } from '@/features/vm-provisioning/context'
import { initialWizardData } from '@/features/vm-provisioning/schema'
import { MediaStep } from '@/features/vm-provisioning/steps/MediaStep'
import { api } from '@/lib/api'
import type { CredentialOptionOut, IsoImageOut } from '@/types/api'

const ISOS: IsoImageOut[] = [
  {
    id: 'iso-windows-2022',
    name: 'Windows Server 2022.iso',
    datacenter_id: 'dc-production',
    datacenter_name: 'Production Datacenter',
    datastore_id: 'datastore-production',
    datastore_name: 'Production Media',
    path: '[Production Media] iso/windows-server-2022.iso',
    size_bytes: 6_442_450_944,
    last_modified: '2026-08-30T08:00:00Z',
  },
  {
    id: 'iso-windows-2025',
    name: 'Windows Server 2025.iso',
    datacenter_id: 'dc-production',
    datacenter_name: 'Production Datacenter',
    datastore_id: 'datastore-production',
    datastore_name: 'Production Media',
    path: '[Production Media] iso/windows-server-2025.iso',
    size_bytes: 7_516_192_768,
    last_modified: '2026-08-29T08:00:00Z',
  },
]

const PRODUCT_KEYS: CredentialOptionOut[] = [
  { name: 'windows-server-2025-standard', purpose: 'windows_product_key', revision: 2, updated_at: null },
]

function StateProbe() {
  const { data } = useWizard()
  return <output data-testid="wizard-state">{JSON.stringify(data)}</output>
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderStep(selected: { iso_id?: string; product_key_secret_ref?: string } = {}) {
  const draft = initialWizardData()
  draft.vcenter_id = 'vc-primary'
  draft.datacenter_id = 'dc-production'
  draft.iso_id = selected.iso_id ?? ''
  draft.product_key_secret_ref = selected.product_key_secret_ref ?? ''
  localStorage.setItem('infraops.provisioning-draft.v3', JSON.stringify(draft))

  return render(
    <QueryClientProvider client={createQueryClient()}>
      <WizardProvider>
        <MediaStep />
        <StateProbe />
      </WizardProvider>
    </QueryClientProvider>,
  )
}

function wizardState() {
  return JSON.parse(screen.getByTestId('wizard-state').textContent ?? '{}') as {
    iso_id: string
    product_key_secret_ref: string
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('MediaStep Windows installation media', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(api, 'provisioningCredentials').mockResolvedValue(PRODUCT_KEYS)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('lists only the datacenter ISOs and offers no way to skip installation media', async () => {
    const request = deferred<IsoImageOut[]>()
    const isos = vi.spyOn(api, 'isos').mockImplementation(() => request.promise)

    renderStep()

    expect(await screen.findByText('Loading ISO images')).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /without an ISO/i })).not.toBeInTheDocument()

    await act(async () => request.resolve(ISOS))

    const group = await screen.findByRole('radiogroup', { name: 'ISO images' })
    expect(within(group).getAllByRole('radio')).toHaveLength(ISOS.length)
    await waitFor(() => expect(isos).toHaveBeenCalledWith('vc-primary', 'dc-production'))

    fireEvent.click(within(group).getByRole('radio', { name: /Windows Server 2025\.iso/i }))
    expect(wizardState().iso_id).toBe('iso-windows-2025')
  })

  it('offers stored product keys and defaults to none', async () => {
    vi.spyOn(api, 'isos').mockResolvedValue(ISOS)

    renderStep()

    const select = await screen.findByRole('combobox', { name: 'Windows product key' })
    expect(select).toHaveValue('')
    await screen.findByRole('option', { name: /windows-server-2025-standard · revision 2/ })
    expect(api.provisioningCredentials).toHaveBeenCalledWith('windows_product_key')

    fireEvent.change(select, { target: { value: 'windows-server-2025-standard' } })
    expect(wizardState().product_key_secret_ref).toBe('windows-server-2025-standard')
  })

  it('blocks media selection when datacenter ISO discovery fails', async () => {
    vi.spyOn(api, 'isos').mockRejectedValue(new Error('ISO inventory timed out.'))

    renderStep()

    expect(await screen.findByText('ISO images could not be loaded')).toBeInTheDocument()
    expect(screen.getByText('ISO inventory timed out.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Try again/i })).toBeEnabled()
  })

  it('clears saved selections that are absent from the current inventory', async () => {
    vi.spyOn(api, 'isos').mockResolvedValue(ISOS)

    renderStep({ iso_id: 'iso-from-another-datacenter', product_key_secret_ref: 'deleted-key' })

    await screen.findByRole('radiogroup', { name: 'ISO images' })
    await waitFor(() => expect(wizardState().iso_id).toBe(''))
    await waitFor(() => expect(wizardState().product_key_secret_ref).toBe(''))
  })
})
