import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Stepper } from '@/components/ui/stepper'

const STEPS = [
  { key: 'one', title: 'Infrastructure' },
  { key: 'two', title: 'Compute' },
  { key: 'three', title: 'Review & Provision' },
]

describe('Stepper', () => {
  it('marks completed, current and upcoming steps', () => {
    const onClick = vi.fn()
    render(<Stepper steps={STEPS} currentIndex={1} onStepClick={onClick} />)

    const current = screen.getByText('Compute')
    expect(current).toHaveClass('font-semibold')

    const upcoming = screen.getByText('Review & Provision')
    expect(upcoming).toBeDisabled()

    // Completed step label is clickable to navigate back.
    const completed = screen.getByText('Infrastructure')
    expect(completed).not.toBeDisabled()
  })

  it('invokes onStepClick only for earlier steps', () => {
    const onClick = vi.fn()
    render(<Stepper steps={STEPS} currentIndex={2} onStepClick={onClick} />)

    screen.getByText('Infrastructure').click()
    expect(onClick).toHaveBeenCalledWith(0)

    onClick.mockClear()
    // Current step is not clickable.
    screen.getByText('Review & Provision').click()
    expect(onClick).not.toHaveBeenCalled()
  })

  it('flags error state on the current step', () => {
    render(<Stepper steps={STEPS} currentIndex={1} errorKeys={['two']} />)
    const navigation = screen.getByRole('navigation')
    const errorIndicator = Array.from(navigation.querySelectorAll('button')).find(
      (button) => button.textContent === '!',
    )
    expect(errorIndicator).toBeDefined()
  })
})
