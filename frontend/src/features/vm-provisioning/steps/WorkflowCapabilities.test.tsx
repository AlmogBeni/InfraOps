import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { WizardProvider } from '@/features/vm-provisioning/context'
import { initialWizardData } from '@/features/vm-provisioning/schema'
import { ConfigurationStep } from '@/features/vm-provisioning/steps/ConfigurationStep'
import { NetworkStep } from '@/features/vm-provisioning/steps/NetworkStep'
import { api } from '@/lib/api'

vi.mock('@/features/vm-provisioning/steps/HardwareStep', () => ({
  HardwareStep: () => <div>Hardware controls</div>,
}))
vi.mock('@/features/vm-provisioning/steps/StorageStep', () => ({
  StorageStep: () => <div>Storage controls</div>,
}))
vi.mock('@/features/vm-provisioning/steps/OsStep', () => ({
  OsStep: () => <div>Join an Active Directory domain</div>,
}))
vi.mock('@/features/vm-provisioning/steps/CertificatesStep', () => ({
  CertificatesStep: () => <div>Certificate controls</div>,
}))
vi.mock('@/features/vm-provisioning/steps/ApplicationsStep', () => ({
  ApplicationsStep: () => <div>Application controls</div>,
}))

function renderWithWizard(content: ReactNode) {
  const draft = initialWizardData()
  draft.iso_id = 'iso-windows'
  draft.vcenter_id = 'vc-primary'
  draft.datacenter_id = 'dc-production'
  localStorage.setItem('infraops.provisioning-draft.v3', JSON.stringify(draft))

  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <WizardProvider>{content}</WizardProvider>
    </QueryClientProvider>,
  )
}

describe('VM workflow capability visibility', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('always shows guest automation, including domain join, without a collapsed disclosure', () => {
    renderWithWizard(<ConfigurationStep />)

    expect(screen.getByText('Guest operating system and automation')).toBeVisible()
    expect(screen.getByText('Join an Active Directory domain')).toBeVisible()
    expect(document.querySelector('details')).not.toBeInTheDocument()
  })

  it('enables post-installation DHCP and static addressing', async () => {
    vi.spyOn(api, 'networks').mockResolvedValue([])
    renderWithWizard(<NetworkStep />)

    expect(await screen.findByRole('radio', { name: /DHCP/i })).toBeEnabled()
    expect(screen.getByRole('radio', { name: /Static IPv4/i })).toBeEnabled()
    expect(screen.getByText('Applied after VMware Tools becomes available.')).toBeVisible()
  })
})
