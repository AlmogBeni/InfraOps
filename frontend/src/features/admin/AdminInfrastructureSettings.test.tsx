import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SettingsPage } from '@/features/admin/SettingsPage'
import { VCenterConnectionsPage } from '@/features/admin/VCenterConnectionsPage'
import { api } from '@/lib/api'
import type { PlatformSettingsOut, SecretReferenceOut, VCenterConnectionAdminOut } from '@/types/api'

function renderWithQueryClient(page: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{page}</QueryClientProvider>)
}

const connection: VCenterConnectionAdminOut = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Production vCenter',
  host: 'vcenter.example.test',
  port: 443,
  username_secret_ref: 'vcenter/production/username',
  password_secret_ref: 'vcenter/production/password',
  verify_ssl: true,
  enabled: true,
  notes: 'Primary virtualization control plane',
  connection_state: 'connected',
  last_connection_error: null,
  last_checked_at: '2026-09-01T10:00:00Z',
}

const platformSettings: PlatformSettingsOut = {
  vm_name_policy_regex: '^[A-Z]+-[0-9]+$',
  allowed_installer_roots: ['\\\\fileserver\\software'],
  default_timeouts: {
    clone_minutes: 60,
    vmware_tools_minutes: 20,
    network_configuration_minutes: 10,
    guest_operations_minutes: 30,
  },
  environment_label: 'Production',
}

const vcenterCredential: SecretReferenceOut = {
  id: '22222222-2222-4222-8222-222222222222',
  name: 'vcenter/lab',
  provider: 'database',
  purpose: 'vcenter',
  description: 'Lab vCenter service account',
  meta: {},
  configured: true,
  revision: 1,
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
}

describe('admin infrastructure and settings redesign', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.admin, 'credentials').mockResolvedValue([vcenterCredential])
  })

  it('keeps credential references off vCenter cards and confirms removal in-app', async () => {
    vi.spyOn(api.admin, 'vcenters').mockResolvedValue([connection])
    const remove = vi.spyOn(api.admin, 'deleteVCenter').mockResolvedValue(undefined)

    renderWithQueryClient(<VCenterConnectionsPage />)

    expect(await screen.findByText('Production vCenter')).toBeInTheDocument()
    expect(screen.queryByText(connection.username_secret_ref)).not.toBeInTheDocument()
    expect(screen.queryByText(connection.password_secret_ref)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Remove Production vCenter' }))
    expect(screen.getByRole('dialog', { name: 'Remove this vCenter connection?' })).toBeInTheDocument()
    expect(remove).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Remove connection' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith(connection.id))
  })

  it('keeps vCenter save disabled until required fields and a valid port are present', async () => {
    vi.spyOn(api.admin, 'vcenters').mockResolvedValue([])

    renderWithQueryClient(<VCenterConnectionsPage />)

    const addButtons = await screen.findAllByRole('button', { name: 'Add vCenter' })
    fireEvent.click(addButtons[0])
    const saveButton = screen.getByRole('button', { name: 'Add connection' })
    expect(saveButton).toBeDisabled()

    fireEvent.change(screen.getByRole('textbox', { name: /Connection name/ }), { target: { value: 'Lab vCenter' } })
    fireEvent.change(screen.getByRole('textbox', { name: /Server hostname/ }), { target: { value: 'lab-vcenter.example.test' } })
    fireEvent.change(screen.getByRole('combobox', { name: /vCenter service account/ }), { target: { value: 'vcenter/lab' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Port' }), { target: { value: '70000' } })

    expect(saveButton).toBeDisabled()
    expect(screen.getByText('Use a whole number from 1 to 65,535.')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('spinbutton', { name: 'Port' }), { target: { value: '443' } })
    expect(saveButton).toBeEnabled()
  })

  it('submits only API-supported fields when creating a vCenter connection', async () => {
    vi.spyOn(api.admin, 'vcenters').mockResolvedValue([])
    const create = vi.spyOn(api.admin, 'createVCenter').mockResolvedValue(connection)

    renderWithQueryClient(<VCenterConnectionsPage />)

    const addButtons = await screen.findAllByRole('button', { name: 'Add vCenter' })
    fireEvent.click(addButtons[0])
    fireEvent.change(screen.getByRole('textbox', { name: /Connection name/ }), { target: { value: ' Lab vCenter ' } })
    fireEvent.change(screen.getByRole('textbox', { name: /Server hostname/ }), { target: { value: ' lab-vcenter.example.test ' } })
    fireEvent.change(screen.getByRole('combobox', { name: /vCenter service account/ }), { target: { value: 'vcenter/lab' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add connection' }))

    await waitFor(() => expect(create).toHaveBeenCalledWith({
      name: 'Lab vCenter',
      host: 'lab-vcenter.example.test',
      port: 443,
      username_secret_ref: 'vcenter/lab/username',
      password_secret_ref: 'vcenter/lab/password',
      verify_ssl: true,
      notes: '',
    }))
    expect(create.mock.calls[0]?.[0]).not.toHaveProperty('credential_secret_ref')
  })

  it('submits the established platform settings payload after an edit', async () => {
    vi.spyOn(api.admin, 'settings').mockResolvedValue(platformSettings)
    vi.spyOn(api.admin, 'roles').mockResolvedValue([])
    const update = vi.spyOn(api.admin, 'updateSettings').mockImplementation(async (payload) => payload as unknown as PlatformSettingsOut)

    renderWithQueryClient(<SettingsPage />)

    const namingRule = await screen.findByRole('textbox', { name: 'Naming rule (advanced)' })
    const saveButton = screen.getByRole('button', { name: 'Save changes' })
    fireEvent.change(namingRule, { target: { value: '(?i)^srv' } })
    expect(saveButton).toBeEnabled()
    fireEvent.change(namingRule, { target: { value: platformSettings.vm_name_policy_regex } })

    const cloneTimeout = screen.getByRole('spinbutton', { name: 'Clone virtual machine' })
    fireEvent.change(cloneTimeout, { target: { value: '0' } })
    expect(saveButton).toBeDisabled()
    expect(screen.getByText('Enter a value from 1 to 240 minutes.')).toBeInTheDocument()
    fireEvent.change(cloneTimeout, { target: { value: '60' } })

    const repositoryRoots = screen.getByRole('textbox', { name: 'Network repository roots' })
    fireEvent.change(repositoryRoots, { target: { value: '\\\\fileserver\\software\n' } })
    expect(repositoryRoots).toHaveValue('\\\\fileserver\\software\n')

    const environmentLabel = await screen.findByRole('textbox', { name: 'Workspace label' })
    fireEvent.change(environmentLabel, { target: { value: 'Staging' } })
    expect(saveButton).toBeEnabled()
    fireEvent.click(saveButton)

    await waitFor(() => expect(update).toHaveBeenCalledWith({
      vm_name_policy_regex: platformSettings.vm_name_policy_regex,
      allowed_installer_roots: platformSettings.allowed_installer_roots,
      default_timeouts: platformSettings.default_timeouts,
      environment_label: 'Staging',
    }))
  })
})
