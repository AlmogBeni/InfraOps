import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select, Textarea } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import type { ApplicationOut, DetectionMethod } from '@/types/api'

interface AppForm {
  name: string
  version: string
  description: string
  installer_type: 'MSI' | 'EXE' | 'POWERSHELL'
  installer_path: string
  install_arguments: string
  detection_method: DetectionMethod
  product_code: string
  key_path: string
  value_name: string
  file_path: string
  service_name: string
  script: string
  timeout_seconds: number
  reboot_required: boolean
  enabled: boolean
  dependency_ids: string[]
}

const EMPTY_FORM: AppForm = {
  name: '',
  version: '',
  description: '',
  installer_type: 'MSI',
  installer_path: '',
  install_arguments: '/qn /norestart',
  detection_method: 'MSI_PRODUCT_CODE',
  product_code: '',
  key_path: '',
  value_name: '',
  file_path: '',
  service_name: '',
  script: '',
  timeout_seconds: 600,
  reboot_required: false,
  enabled: true,
  dependency_ids: [],
}

function formToBody(form: AppForm): Record<string, unknown> {
  const detection_config =
    form.detection_method === 'MSI_PRODUCT_CODE'
      ? { product_code: form.product_code }
      : form.detection_method === 'REGISTRY_KEY'
        ? { key_path: form.key_path, ...(form.value_name ? { value_name: form.value_name } : {}) }
        : form.detection_method === 'FILE_EXISTS'
          ? { path: form.file_path }
          : form.detection_method === 'SERVICE_EXISTS'
            ? { service_name: form.service_name }
            : { script: form.script }
  return {
    name: form.name,
    version: form.version,
    description: form.description,
    installer_type: form.installer_type,
    installer_path: form.installer_path,
    install_arguments: form.install_arguments,
    detection_method: form.detection_method,
    detection_config,
    timeout_seconds: Number(form.timeout_seconds),
    reboot_required: form.reboot_required,
    enabled: form.enabled,
    dependency_ids: form.dependency_ids,
  }
}

function appToForm(app: ApplicationOut): AppForm {
  const config = app.detection_config as Record<string, string>
  return {
    ...EMPTY_FORM,
    name: app.name,
    version: app.version,
    description: app.description,
    installer_type: app.installer_type,
    installer_path: app.installer_path,
    install_arguments: app.install_arguments,
    detection_method: app.detection_method,
    product_code: config['product_code'] ?? '',
    key_path: config['key_path'] ?? '',
    value_name: config['value_name'] ?? '',
    file_path: config['path'] ?? '',
    service_name: config['service_name'] ?? '',
    script: config['script'] ?? '',
    timeout_seconds: app.timeout_seconds,
    reboot_required: app.reboot_required,
    enabled: app.enabled,
    dependency_ids: [...app.dependency_ids],
  }
}

export function ApplicationCatalogPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<AppForm>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)

  const applications = useQuery({ queryKey: ['admin-applications'], queryFn: () => api.admin.applications() })

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ['admin-applications'] })
    void queryClient.invalidateQueries({ queryKey: ['applications'] })
  }

  function openCreate() {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setFormError(null)
    setDialogOpen(true)
  }

  function openEdit(app: ApplicationOut) {
    setEditingId(app.id)
    setForm(appToForm(app))
    setFormError(null)
    setDialogOpen(true)
  }

  const save = useMutation({
    mutationFn: async () => {
      const body = formToBody(form)
      if (editingId) return api.admin.updateApplication(editingId, body)
      return api.admin.createApplication(body)
    },
    onSuccess: () => {
      setDialogOpen(false)
      invalidate()
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : 'Save failed.'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.admin.deleteApplication(id),
    onSuccess: invalidate,
  })

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.admin.updateApplication(id, { enabled }),
    onSuccess: invalidate,
  })

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Software governance"
        title="Approved application catalog"
        description="Control installer sources, silent arguments, detection rules, timeouts, reboot behavior, and dependency ordering."
        actions={<Button size="sm" onClick={openCreate}><Plus className="h-4 w-4" /> Add application</Button>}
        meta={<span>{applications.data?.length ?? 0} catalog entry(s)</span>}
      />

      {applications.isLoading ? (
        <div className="flex h-40 items-center justify-center"><Spinner /></div>
      ) : applications.isError ? (
        <Alert tone="danger" title="Application catalog unavailable">
          Approved applications could not be loaded.{' '}
          <Button size="sm" variant="secondary" onClick={() => void applications.refetch()}>Retry</Button>
        </Alert>
      ) : (applications.data ?? []).length === 0 ? (
        <EmptyState
          title="No approved applications"
          description="Create a governed installer entry before operators can include software in provisioning requests."
          action={<Button size="sm" onClick={openCreate}><Plus className="h-3.5 w-3.5" /> Add application</Button>}
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Version</Th>
              <Th>Installer</Th>
              <Th>Detection</Th>
              <Th>Timeout</Th>
              <Th>Enabled</Th>
              <Th className="text-right">Actions</Th>
            </tr>
          </thead>
          <tbody>
            {(applications.data ?? []).map((app) => (
              <Tr key={app.id}>
                <Td className="font-medium text-slate-800">
                  {app.name}
                  {app.reboot_required && <Badge tone="warning" className="ml-2">reboot</Badge>}
                </Td>
                <Td>{app.version || '—'}</Td>
                <Td>
                  <Badge tone="info">{app.installer_type}</Badge>{' '}
                  <span className="font-mono text-[11px] text-slate-500">{app.installer_path}</span>
                </Td>
                <Td className="text-xs text-slate-500">{app.detection_method}</Td>
                <Td>{app.timeout_seconds}s</Td>
                <Td>
                  <Checkbox label="" checked={app.enabled} aria-label={`Toggle ${app.name}`}
                            onChange={(event) => toggleEnabled.mutate({ id: app.id, enabled: event.target.checked })} />
                </Td>
                <Td className="text-right">
                  <div className="inline-flex gap-1">
                    <Button size="sm" variant="secondary" onClick={() => openEdit(app)}>Edit</Button>
                    <Button size="sm" variant="ghost" aria-label={`Delete ${app.name}`}
                            onClick={() => {
                              if (window.confirm(`Delete application '${app.name}'?`)) remove.mutate(app.id)
                            }}>
                      <Trash2 className="h-3.5 w-3.5 text-red-500" />
                    </Button>
                  </div>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={editingId ? 'Edit application' : 'Add application'}
        wide
        footer={
          <>
            <Button variant="secondary" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
          </>
        }
      >
        {formError && <div className="mb-3"><Alert tone="danger">{formError}</Alert></div>}

        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          <FormRow label="Name" htmlFor="app-name" required>
            <Input id="app-name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
          </FormRow>
          <FormRow label="Version" htmlFor="app-version">
            <Input id="app-version" value={form.version} onChange={(event) => setForm({ ...form, version: event.target.value })} />
          </FormRow>
        </div>

        <FormRow label="Description" htmlFor="app-desc">
          <Input id="app-desc" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
        </FormRow>

        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-[160px_1fr]">
          <FormRow label="Installer type" htmlFor="app-type">
            <Select id="app-type" value={form.installer_type}
                    onChange={(event) => setForm({ ...form, installer_type: event.target.value as AppForm['installer_type'] })}>
              <option value="MSI">MSI</option>
              <option value="EXE">EXE</option>
              <option value="POWERSHELL">PowerShell</option>
            </Select>
          </FormRow>
          <FormRow label="Installer path (UNC)" htmlFor="app-path" required
                   hint="Must reside under an approved software repository root.">
            <Input id="app-path" className="font-mono text-xs" value={form.installer_path}
                   onChange={(event) => setForm({ ...form, installer_path: event.target.value })} />
          </FormRow>
        </div>

        <FormRow label="Silent install arguments" htmlFor="app-args"
                 hint="Admin-defined only — appended to the controlled command line.">
          <Input id="app-args" className="font-mono text-xs" value={form.install_arguments}
                 onChange={(event) => setForm({ ...form, install_arguments: event.target.value })} />
        </FormRow>

        <FormRow label="Detection method" htmlFor="app-detect">
          <Select id="app-detect" value={form.detection_method}
                  onChange={(event) => setForm({ ...form, detection_method: event.target.value as DetectionMethod })}>
            <option value="MSI_PRODUCT_CODE">MSI product code</option>
            <option value="REGISTRY_KEY">Registry key</option>
            <option value="FILE_EXISTS">File exists</option>
            <option value="SERVICE_EXISTS">Windows service exists</option>
            <option value="SCRIPT">Custom PowerShell detection (exit 0 = installed)</option>
          </Select>
        </FormRow>

        {form.detection_method === 'MSI_PRODUCT_CODE' && (
          <FormRow label="Product code (GUID)" htmlFor="detect-guid" required>
            <Input id="detect-guid" className="font-mono text-xs" placeholder="{A1B2C3D4-...}"
                   value={form.product_code} onChange={(event) => setForm({ ...form, product_code: event.target.value })} />
          </FormRow>
        )}
        {form.detection_method === 'REGISTRY_KEY' && (
          <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-[1fr_180px]">
            <FormRow label="Key path" htmlFor="detect-key" required hint="HKLM\… or HKCU\…">
              <Input id="detect-key" className="font-mono text-xs" value={form.key_path}
                     onChange={(event) => setForm({ ...form, key_path: event.target.value })} />
            </FormRow>
            <FormRow label="Value name" htmlFor="detect-value">
              <Input id="detect-value" className="font-mono text-xs" value={form.value_name}
                     onChange={(event) => setForm({ ...form, value_name: event.target.value })} />
            </FormRow>
          </div>
        )}
        {form.detection_method === 'FILE_EXISTS' && (
          <FormRow label="File path" htmlFor="detect-file" required>
            <Input id="detect-file" className="font-mono text-xs" value={form.file_path}
                   onChange={(event) => setForm({ ...form, file_path: event.target.value })} />
          </FormRow>
        )}
        {form.detection_method === 'SERVICE_EXISTS' && (
          <FormRow label="Service name" htmlFor="detect-service" required>
            <Input id="detect-service" className="font-mono text-xs" value={form.service_name}
                   onChange={(event) => setForm({ ...form, service_name: event.target.value })} />
          </FormRow>
        )}
        {form.detection_method === 'SCRIPT' && (
          <FormRow label="Detection script" htmlFor="detect-script" required
                   hint="Exit 0 = installed, exit 1 = not installed. Max 8000 characters.">
            <Textarea id="detect-script" rows={5} className="font-mono text-xs" value={form.script}
                      onChange={(event) => setForm({ ...form, script: event.target.value })} />
          </FormRow>
        )}

        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          <FormRow label="Timeout (seconds)" htmlFor="app-timeout">
            <Input id="app-timeout" type="number" min={30} max={14400} value={form.timeout_seconds}
                   onChange={(event) => setForm({ ...form, timeout_seconds: Number(event.target.value) })} />
          </FormRow>
          <div className="space-y-2 pt-6">
            <Checkbox label="Reboot required after installation" checked={form.reboot_required}
                      onChange={(event) => setForm({ ...form, reboot_required: event.target.checked })} />
            <Checkbox label="Enabled (selectable by operators)" checked={form.enabled}
                      onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />
          </div>
        </div>

        {(applications.data ?? []).filter((app) => app.id !== editingId).length > 0 && (
          <div>
            <p className="field-label">Dependencies (installed first)</p>
            <div className="grid grid-cols-1 gap-1 rounded-md border border-slate-200 p-3 sm:grid-cols-2">
              {(applications.data ?? [])
                .filter((app) => app.id !== editingId)
                .map((app) => (
                  <Checkbox
                    key={app.id}
                    label={app.name}
                    checked={form.dependency_ids.includes(app.id)}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        dependency_ids: event.target.checked
                          ? [...form.dependency_ids, app.id]
                          : form.dependency_ids.filter((id) => id !== app.id),
                      })
                    }
                  />
                ))}
            </div>
          </div>
        )}
      </Dialog>
    </div>
  )
}
