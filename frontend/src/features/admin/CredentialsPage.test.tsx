import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CredentialsPage } from '@/features/admin/CredentialsPage'
import { api } from '@/lib/api'

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={client}>
      <CredentialsPage />
    </QueryClientProvider>,
  )
}

describe('CredentialsPage create dialog', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps focus in the reference name while entering a controlled value', async () => {
    vi.spyOn(api.admin, 'credentials').mockResolvedValue([])
    renderPage()

    const addButtons = await screen.findAllByRole('button', { name: /Add credential/i })
    fireEvent.click(addButtons[0])

    const nameInput = screen.getByLabelText(/Reference name/i)
    fireEvent.focus(nameInput)
    fireEvent.change(nameInput, { target: { value: 'g' } })

    expect(nameInput).toHaveValue('g')
    expect(nameInput).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Save credential' })).toBeDisabled()
    expect(screen.getByText('Enter a valid secret reference from 2 to 150 characters.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Close dialog' })[0]).not.toHaveFocus()
  })
})
