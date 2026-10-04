import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { WizardProvider, useWizard } from '@/features/vm-provisioning/context'
import { initialWizardData } from '@/features/vm-provisioning/schema'

const STORAGE_KEY = 'infraops.provisioning-draft.v3'

function Harness() {
  const wizard = useWizard()
  return (
    <>
      <button type="button" onClick={() => wizard.update({ datacenter_id: 'datacenter-2' })}>
        Change datacenter
      </button>
      <button type="button" onClick={() => wizard.goTo(5)}>Open review</button>
      <button type="button" onClick={() => wizard.setErrors({ ip_address: 'Enter a valid IPv4 address.' })}>
        Set IP error
      </button>
      <button type="button" onClick={() => wizard.update({ ip_address: '192.168.77.52' })}>
        Correct IP
      </button>
      <output data-testid="wizard-state">{JSON.stringify(wizard.data)}</output>
      <output data-testid="wizard-step">{wizard.currentIndex}</output>
      <output data-testid="wizard-errors">{JSON.stringify(wizard.errors)}</output>
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
    expect(state.iso_id).toBe('')
    expect(state.disks[0].datastore_id).toBeNull()
  })

  it('drops drafts written by the previous wizard', () => {
    localStorage.setItem('infraops.provisioning-draft.v2', JSON.stringify({ vm_name: 'OLD-DRAFT', iso_id: null }))

    render(<WizardProvider><Harness /></WizardProvider>)

    const state = JSON.parse(screen.getByTestId('wizard-state').textContent ?? '{}')
    expect(state.vm_name).toBe('')
    expect(state.iso_id).toBe('')
    expect(state).not.toHaveProperty('source_type')
    expect(localStorage.getItem('infraops.provisioning-draft.v2')).toBeNull()
  })

  it('does not let the stepper skip past an invalid earlier step', () => {
    render(<WizardProvider><Harness /></WizardProvider>)

    fireEvent.click(screen.getByRole('button', { name: 'Open review' }))

    expect(screen.getByTestId('wizard-step')).toHaveTextContent('0')
  })

  it('clears a stale field error when that field is corrected', () => {
    render(<WizardProvider><Harness /></WizardProvider>)

    fireEvent.click(screen.getByRole('button', { name: 'Set IP error' }))
    expect(screen.getByTestId('wizard-errors')).toHaveTextContent('Enter a valid IPv4 address.')

    fireEvent.click(screen.getByRole('button', { name: 'Correct IP' }))
    expect(screen.getByTestId('wizard-errors')).toHaveTextContent('{}')
  })
})
