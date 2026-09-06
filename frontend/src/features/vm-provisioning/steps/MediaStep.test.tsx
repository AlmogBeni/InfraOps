import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WizardProvider, useWizard } from '@/features/vm-provisioning/context'
import { initialWizardData } from '@/features/vm-provisioning/schema'
import { MediaStep } from '@/features/vm-provisioning/steps/MediaStep'
import { api } from '@/lib/api'
import type { IsoImageOut, TemplateOut } from '@/types/api'

const ISOS: IsoImageOut[] = [
  {
    id: 'iso-ubuntu',
    name: 'Ubuntu Server 24.04.iso',
    datacenter_id: 'dc-production',
    datacenter_name: 'Production Datacenter',
    datastore_id: 'datastore-production',
    datastore_name: 'Production Media',
    path: '[Production Media] iso/ubuntu-24.04.iso',
    size_bytes: 6_442_450_944,
    last_modified: '2026-08-30T08:00:00Z',
  },
  {
    id: 'iso-windows',
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

const PACKAGES: TemplateOut[] = [
  {
    id: 'pkg-firewall-ovf',
    name: 'Branch Firewall',
    type: 'OVF',
    description: 'Hardened branch firewall package.',
    datacenter_id: 'dc-production',
    datacenter_name: 'Production Datacenter',
    storage_name: 'Production Library',
    location: 'Production Library / Network',
    size_bytes: 1_073_741_824,
    last_modified: '2026-08-30T08:00:00Z',
  },
  {
    id: 'pkg-monitoring-ova',
    name: 'Monitoring Collector',
    type: 'OVA',
    description: 'Monitoring and telemetry collector.',
    datacenter_id: 'dc-production',
    datacenter_name: 'Production Datacenter',
    storage_name: 'Production Library',
    location: 'Production Library / Operations',
    size_bytes: 2_147_483_648,
    last_modified: '2026-08-29T08:00:00Z',
  },
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

function renderStep(
  sourceType: 'blank' | 'template',
  selected: { template_id?: string; iso_id?: string | null } = {},
) {
  const draft = initialWizardData()
  draft.source_type = sourceType
  draft.vcenter_id = 'vc-primary'
  draft.datacenter_id = 'dc-production'
  draft.template_id = selected.template_id ?? ''
  draft.iso_id = selected.iso_id ?? null
  localStorage.setItem('infraops.provisioning-draft.v2', JSON.stringify(draft))

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
    iso_id: string | null
    template_id: string
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('MediaStep datacenter-scoped resources', () => {
  beforeEach(() => {
    localStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('requires a discovered ISO and offers only ISO names returned for the target datacenter', async () => {
    const request = deferred<IsoImageOut[]>()
    const isos = vi.spyOn(api, 'isos').mockImplementation(() => request.promise)
    const templates = vi.spyOn(api, 'templates').mockResolvedValue(PACKAGES)

    renderStep('blank')

    expect(screen.queryByRole('radio', { name: /No ISO/i })).not.toBeInTheDocument()
    expect(await screen.findByText('Loading ISO images')).toBeInTheDocument()
    expect(templates).not.toHaveBeenCalled()

    await act(async () => request.resolve(ISOS))

    const group = await screen.findByRole('radiogroup', { name: 'ISO images' })
    const choices = within(group).getAllByRole('radio')
    expect(choices).toHaveLength(ISOS.length)
    expect(within(group).getByRole('radio', { name: /Ubuntu Server 24\.04\.iso/i })).toBeInTheDocument()
    expect(within(group).getByRole('radio', { name: /Windows Server 2025\.iso/i })).toBeInTheDocument()
    expect(screen.queryByText('Lab Datacenter Installer.iso')).not.toBeInTheDocument()
    await waitFor(() => expect(isos).toHaveBeenCalledWith('vc-primary', 'dc-production'))

    fireEvent.click(within(group).getByRole('radio', { name: /Ubuntu Server 24\.04\.iso/i }))
    expect(wizardState().iso_id).toBe('iso-ubuntu')
  })

  it('shows and selects OVF and OVA packages without querying ISO inventory', async () => {
    const templates = vi.spyOn(api, 'templates').mockResolvedValue(PACKAGES)
    const isos = vi.spyOn(api, 'isos').mockResolvedValue(ISOS)

    renderStep('template')

    const group = await screen.findByRole('radiogroup', { name: 'OVF and OVA packages' })
    expect(within(group).getAllByRole('radio')).toHaveLength(PACKAGES.length)
    expect(within(group).getByRole('radio', { name: /Branch Firewall.*OVF/i })).toBeInTheDocument()
    expect(within(group).getByRole('radio', { name: /Monitoring Collector.*OVA/i })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /No ISO/i })).not.toBeInTheDocument()
    expect(isos).not.toHaveBeenCalled()
    await waitFor(() => expect(templates).toHaveBeenCalledWith('vc-primary', 'dc-production'))

    fireEvent.click(within(group).getByRole('radio', { name: /Monitoring Collector.*OVA/i }))
    expect(wizardState().template_id).toBe('pkg-monitoring-ova')
  })

  it('blocks media selection when datacenter ISO discovery fails', async () => {
    vi.spyOn(api, 'isos').mockRejectedValue(new Error('ISO inventory timed out.'))
    vi.spyOn(api, 'templates').mockResolvedValue(PACKAGES)

    renderStep('blank')

    expect(await screen.findByText('ISO images could not be loaded')).toBeInTheDocument()
    expect(screen.getByText('ISO inventory timed out.')).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /No ISO/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Try again/i })).toBeEnabled()
  })

  it('clears saved source selections that are absent from the current datacenter inventory', async () => {
    vi.spyOn(api, 'isos').mockResolvedValue(ISOS)
    vi.spyOn(api, 'templates').mockResolvedValue(PACKAGES)

    const view = renderStep('blank', { iso_id: 'iso-from-another-datacenter' })
    await screen.findByRole('radiogroup', { name: 'ISO images' })
    await waitFor(() => expect(wizardState().iso_id).toBeNull())

    view.unmount()
    renderStep('template', { template_id: 'removed-package' })
    await screen.findByRole('radiogroup', { name: 'OVF and OVA packages' })
    await waitFor(() => expect(wizardState().template_id).toBe(''))
  })
})
