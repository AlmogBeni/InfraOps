import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CertificatePackagesPage } from '@/features/admin/CertificatePackagesPage'
import { api } from '@/lib/api'

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CertificatePackagesPage />
    </QueryClientProvider>,
  )
}

describe('CertificatePackagesPage file upload', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.admin, 'packages').mockResolvedValue([{
      id: '11111111-1111-4111-8111-111111111111',
      name: 'VM roots',
      description: '',
      enabled: true,
      certificates: [],
      created_at: null,
    }])
  })

  it('accepts, replaces and submits the active local certificate', async () => {
    const register = vi.spyOn(api.admin, 'registerCertificate').mockResolvedValue({})
    const { container } = renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Register certificate/i }))

    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]')
    expect(fileInput).not.toBeNull()
    const firstBody = '-----BEGIN CERTIFICATE-----\nZmlyc3Q=\n-----END CERTIFICATE-----'
    const secondBody = '-----BEGIN CERTIFICATE-----\nc2Vjb25k\n-----END CERTIFICATE-----'
    fireEvent.change(fileInput!, { target: { files: [new File([firstBody], 'first-root.pem')] } })
    expect(await screen.findByText('first-root.pem')).toBeInTheDocument()

    fireEvent.change(fileInput!, { target: { files: [new File([secondBody], 'replacement-root.crt')] } })
    expect(await screen.findByText('replacement-root.crt')).toBeInTheDocument()
    expect(screen.queryByText('first-root.pem')).not.toBeInTheDocument()
    await screen.findByText('Valid PEM certificate file; ready to register.')

    fireEvent.click(screen.getByRole('button', { name: /^Register$/i }))
    await waitFor(() => expect(register).toHaveBeenCalledWith(expect.objectContaining({
      package_id: '11111111-1111-4111-8111-111111111111',
      pem_body: secondBody,
    })))
  })

  it('shows an actionable error for an unsupported file', async () => {
    const { container } = renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Register certificate/i }))
    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]')
    fireEvent.change(fileInput!, { target: { files: [new File(['binary'], 'bundle.pfx')] } })
    expect(await screen.findByText(/Unsupported certificate format/i)).toBeInTheDocument()
  })
})
