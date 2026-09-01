import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { WizardProvider, useWizard } from '@/features/vm-provisioning/context'
import { initialWizardData } from '@/features/vm-provisioning/schema'

const STORAGE_KEY = 'infraops.provisioning-draft.v2'

function Harness() {
  const wizard = useWizard()
  return (
    <>
      <button type="button" onClick={() => wizard.update({ datacenter_id: 'datacenter-2' })}>
        Change datacenter
      </button>
      <button type="button" onClick={() => wizard.goTo(5)}>Open review</button>
      <output data-testid="wizard-state">{JSON.stringify(wizard.data)}</output>
      <output data-testid="wizard-step">{wizard.currentIndex}</output>
    </>
  )
}

describe('WizardProvider datacenter scope', () => {
  beforeEach(() => {
    localStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  it('clears every selection owned by the previous datacenter', () => {
    const draft = initialWizardData()
    Object.assign(draft, {
      source_type: 'blank',
      vcenter_id: 'vcenter-1',
      datacenter_id: 'datacenter-1',
      cluster_id: 'cluster-1',
      host_mode: 'manual',
      host_id: 'host-1',
      resource_pool_id: 'pool-1',
      datastore_id: 'datastore-1',
      network_id: 'network-1',
      iso_id: 'iso-1',
      disks: [{ size_gb: 100, provisioning: 'thin', datastore_id: 'datastore-1' }],
    })
    localStorage.setItem(STORAGE_KEY, JSON.stringify(draft))

    render(<WizardProvider><Harness /></WizardProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Change datacenter' }))

    const state = JSON.parse(screen.getByTestId('wizard-state').textContent ?? '{}')
    expect(state.datacenter_id).toBe('datacenter-2')
    expect(state.cluster_id).toBe('')
    expect(state.host_id).toBeNull()
    expect(state.resource_pool_id).toBeNull()
    expect(state.datastore_id).toBeNull()
    expect(state.network_id).toBe('')
    expect(state.iso_id).toBeNull()
    expect(state.disks[0].datastore_id).toBeNull()
  })

  it('clears stale datacenter-owned selections when a package link supplies a new target', () => {
    const draft = initialWizardData()
    Object.assign(draft, {
      source_type: 'blank',
      vcenter_id: 'vcenter-1',
      datacenter_id: 'datacenter-1',
      cluster_id: 'cluster-1',
      host_mode: 'manual',
      host_id: 'host-1',
      resource_pool_id: 'pool-1',
      datastore_id: 'datastore-1',
      network_id: 'network-1',
      iso_id: 'iso-1',
      hostname: 'stale-host',
      certificate_package_ids: ['certificate-1'],
      application_ids: ['application-1'],
      disks: [{ size_gb: 100, provisioning: 'thin', datastore_id: 'datastore-1' }],
    })
    localStorage.setItem(STORAGE_KEY, JSON.stringify(draft))
    window.history.replaceState(
      {},
      '',
      '/provisioning/new?template_id=package-2&vcenter_id=vcenter-2&datacenter_id=datacenter-2',
    )

    render(<WizardProvider><Harness /></WizardProvider>)

    const state = JSON.parse(screen.getByTestId('wizard-state').textContent ?? '{}')
    expect(state.source_type).toBe('template')
    expect(state.template_id).toBe('package-2')
    expect(state.vcenter_id).toBe('vcenter-2')
    expect(state.datacenter_id).toBe('datacenter-2')
    expect(state.cluster_id).toBe('')
    expect(state.host_id).toBeNull()
    expect(state.resource_pool_id).toBeNull()
    expect(state.datastore_id).toBeNull()
    expect(state.network_id).toBe('')
    expect(state.iso_id).toBeNull()
    expect(state.hostname).toBe('')
    expect(state.certificate_package_ids).toEqual([])
    expect(state.application_ids).toEqual([])
    expect(state.disks[0].datastore_id).toBeNull()
  })

  it('does not let the stepper skip past an invalid earlier step', () => {
    render(<WizardProvider><Harness /></WizardProvider>)

    fireEvent.click(screen.getByRole('button', { name: 'Open review' }))

    expect(screen.getByTestId('wizard-step')).toHaveTextContent('0')
  })
})
