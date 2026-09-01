import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, PlugZap, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import { formatDateTime, humanizeIdentifier } from '@/lib/utils'
import type { VCenterConnectionAdminOut } from '@/types/api'

const EMPTY_FORM = {
  name: '',
  host: '',
  port: 443,
  username_secret_ref: '',
  password_secret_ref: '',
  verify_ssl: true,
  notes: '',
}

export function VCenterConnectionsPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<VCenterConnectionAdminOut | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, string>>({})

  const connections = useQuery({ queryKey: ['admin-vcenters'], queryFn: () => api.admin.vcenters() })

  function openCreate() {
    setEditing(null)
    setForm(EMPTY_FORM)
    setFormError(null)
    setDialogOpen(true)
  }

  function openEdit(connection: VCenterConnectionAdminOut) {
    setEditing(connection)
    setForm({
      name: connection.name,
      host: connection.host,
      port: connection.port,
      username_secret_ref: connection.username_secret_ref,
      password_secret_ref: connection.password_secret_ref,
      verify_ssl: connection.verify_ssl,
      notes: connection.notes,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  const save = useMutation({
    mutationFn: async () => {
      const body = { ...form, port: Number(form.port) }
      if (editing) return api.admin.updateVCenter(editing.id, body)
      return api.admin.createVCenter(body)
    },
    onSuccess: () => {
      setDialogOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['admin-vcenters'] })
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : 'Save failed.'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.admin.deleteVCenter(id),
    onMutate: () => setActionError(null),
    onSuccess: () => {
      setActionError(null)
      void queryClient.invalidateQueries({ queryKey: ['admin-vcenters'] })
    },
    onError: (error) => setActionError(error instanceof Error ? error.message : 'The vCenter connection could not be deleted.'),
  })

  const test = useMutation({
    mutationFn: async (id: string) => ({ id, result: await api.admin.testVCenter(id) }),
    onMutate: () => setActionError(null),
    onSuccess: ({ id, result }) => {
      setActionError(null)
      setTestResults((previous) => ({
        ...previous,
        [id]: result.ok ? `Connected (${result.latency_ms ?? '?'} ms)` : result.detail,
      }))
      void queryClient.invalidateQueries({ queryKey: ['admin-vcenters'] })
    },
    onError: (error) => setActionError(error instanceof Error ? error.message : 'The connection test could not be completed.'),
  })

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Infrastructure administration"
        title="vCenter connection registry"
        description="Manage authenticated vSphere endpoints, TLS verification, external secret references, and connection health."
        actions={<Button size="sm" onClick={openCreate}><Plus className="h-4 w-4" /> Add vCenter</Button>}
        meta={<span>{connections.data?.length ?? 0} configured endpoint(s)</span>}
      />

      {actionError && <Alert tone="danger" title="vCenter action could not be completed">{actionError}</Alert>}

      {connections.isLoading ? (
        <LoadingState title="Loading vCenter connections" description="Checking configured endpoints and their latest health state." />
      ) : connections.isError ? (
        <Alert tone="danger" title="vCenter registry unavailable">
          The connection registry could not be loaded.{' '}
          <Button size="sm" variant="secondary" onClick={() => void connections.refetch()}>Retry</Button>
        </Alert>
      ) : (connections.data ?? []).length === 0 ? (
        <EmptyState
          title="No vCenter endpoints configured"
          description="Add a connection before operators can browse inventory or provision virtual machines."
          action={<Button size="sm" onClick={openCreate}><Plus className="h-3.5 w-3.5" /> Add vCenter</Button>}
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Server</Th>
              <Th>Status</Th>
              <Th>Last Checked</Th>
              <Th className="text-right">Actions</Th>
            </tr>
          </thead>
          <tbody>
            {(connections.data ?? []).map((connection) => (
              <Tr key={connection.id}>
                <Td className="font-medium text-slate-800">
                  {connection.name}
                  {!connection.enabled && <Badge tone="neutral" className="ml-2">disabled</Badge>}
                </Td>
                <Td className="font-mono text-xs">{connection.host}:{connection.port}</Td>
                <Td>
                  <Badge
                    tone={
                      connection.connection_state === 'connected'
                        ? 'success'
                        : connection.connection_state === 'error'
                          ? 'danger'
                          : 'neutral'
                    }
                  >
                    {humanizeIdentifier(connection.connection_state)}
                  </Badge>
                  {testResults[connection.id] && (
                    <span className="ml-2 text-xs text-slate-500">{testResults[connection.id]}</span>
                  )}
                </Td>
                <Td>{formatDateTime(connection.last_checked_at)}</Td>
                <Td className="text-right">
                  <div className="inline-flex gap-1">
                    <Button size="sm" variant="secondary" loading={test.isPending && test.variables === connection.id}
                            onClick={() => test.mutate(connection.id)}>
                      <PlugZap className="h-3.5 w-3.5" /> Test
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => openEdit(connection)} aria-label={`Edit ${connection.name}`}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost"
                            aria-label={`Delete ${connection.name}`}
                            onClick={() => {
                              if (window.confirm(`Delete connection '${connection.name}'?`)) remove.mutate(connection.id)
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
        title={editing ? `Edit ${editing.name}` : 'Add vCenter connection'}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
          </>
        }
      >
        {formError && (
          <div className="mb-3">
            <Alert tone="danger">{formError}</Alert>
          </div>
        )}
        <FormRow label="Name" htmlFor="vc-name" required>
          <Input id="vc-name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        </FormRow>
        <div className="grid grid-cols-[1fr_100px] gap-3">
          <FormRow label="Host" htmlFor="vc-host" required>
            <Input id="vc-host" className="font-mono" value={form.host}
                   onChange={(event) => setForm({ ...form, host: event.target.value })} />
          </FormRow>
          <FormRow label="Port" htmlFor="vc-port">
            <Input id="vc-port" type="number" value={form.port}
                   onChange={(event) => setForm({ ...form, port: Number(event.target.value) })} />
          </FormRow>
        </div>
        <FormRow label="Username secret reference" htmlFor="vc-user-ref" required
                 hint="For example, vcenter/username — resolved via the secrets provider at connect time.">
          <Input id="vc-user-ref" className="font-mono" value={form.username_secret_ref}
                 onChange={(event) => setForm({ ...form, username_secret_ref: event.target.value })} />
        </FormRow>
        <FormRow label="Password secret reference" htmlFor="vc-pass-ref" required>
          <Input id="vc-pass-ref" className="font-mono" value={form.password_secret_ref}
                 onChange={(event) => setForm({ ...form, password_secret_ref: event.target.value })} />
        </FormRow>
        <FormRow label="Notes" htmlFor="vc-notes">
          <Input id="vc-notes" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
        </FormRow>
        <Checkbox label="Verify TLS certificate" checked={form.verify_ssl}
                  onChange={(event) => setForm({ ...form, verify_ssl: event.target.checked })} />
      </Dialog>
    </div>
  )
}
