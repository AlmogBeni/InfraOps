import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { WizardProvider, useWizard } from '@/features/vm-provisioning/context'
import { initialWizardData } from '@/features/vm-provisioning/schema'
import { SourceStep } from '@/features/vm-provisioning/steps/SourceStep'

function StateProbe() {
  const { data } = useWizard()
  return <output data-testid="wizard-state">{JSON.stringify(data)}</output>
}

describe('SourceStep', () => {
  beforeEach(() => localStorage.clear())

  it('clears template-only state when switching to a blank VM', () => {
    const draft = initialWizardData()
    draft.source_type = 'template'
    draft.template_id = 'vm-template-x'
    draft.certificate_package_ids = ['package-1']
    draft.application_ids = ['application-1']
    localStorage.setItem('infraops.provisioning-draft.v2', JSON.stringify(draft))

    render(
      <WizardProvider>
        <SourceStep />
        <StateProbe />
      </WizardProvider>,
    )
    fireEvent.click(screen.getByRole('radio', { name: /Blank Virtual Machine/i }))

    const state = JSON.parse(screen.getByTestId('wizard-state').textContent ?? '{}')
    expect(state.source_type).toBe('blank')
    expect(state.template_id).toBe('')
    expect(state.certificate_package_ids).toEqual([])
    expect(state.application_ids).toEqual([])
  })
})
