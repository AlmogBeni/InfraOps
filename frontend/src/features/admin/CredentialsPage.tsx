import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { FormRow, Input, Select } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'

export function CredentialsPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState({ name: '', provider: 'env', description: '' })
  const [formError, setFormError] = useState<string | null>(null)

  const credentials = useQuery({ queryKey: ['admin-credentials'], queryFn: () => api.admin.credentials() })

  const create = useMutation({
    mutationFn: () => api.admin.createCredential(form),
    onSuccess: () => {
      setDialogOpen(false)
      setForm({ name: '', provider: 'env', description: '' })
      void queryClient.invalidateQueries({ queryKey: ['admin-credentials'] })
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : 'Create failed.'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.admin.deleteCredential(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['admin-credentials'] }),
  })

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Security administration"
        title="Credential and secret references"
        description="Register logical references to external secret providers. Secret material is never displayed, logged, persisted in jobs, or exported."
        actions={<Button size="sm" onClick={() => { setDialogOpen(true); setFormError(null) }}><Plus className="h-4 w-4" /> Add reference</Button>}
        meta={<span>{credentials.data?.length ?? 0} registered reference(s)</span>}
      />

      {credentials.isLoading ? (
        <div className="flex h-40 items-center justify-center"><Spinner /></div>
      ) : credentials.isError ? (
        <Alert tone="danger" title="Secret reference registry unavailable">
          Credential references could not be loaded.{' '}
          <Button size="sm" variant="secondary" onClick={() => void credentials.refetch()}>Retry</Button>
        </Alert>
      ) : (credentials.data ?? []).length === 0 ? (
        <EmptyState
          title="No credential references"
          description="Register a logical provider reference without exposing the underlying secret material."
          action={<Button size="sm" onClick={() => { setDialogOpen(true); setFormError(null) }}><Plus className="h-3.5 w-3.5" /> Add reference</Button>}
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Provider</Th>
              <Th>Description</Th>
              <Th>Expected variables (env provider)</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {(credentials.data ?? []).map((credential) => (
              <Tr key={credential.id}>
                <Td className="font-mono text-xs font-medium text-slate-800">{credential.name}</Td>
                <Td><Badge tone="info">{credential.provider}</Badge></Td>
                <Td className="text-slate-600">{credential.description}</Td>
                <Td className="font-mono text-[11px] text-slate-400">
                  SECRETS_{credential.name.replaceAll(/[^\w]/g, '_').toUpperCase()}_USERNAME / _PASSWORD
                </Td>
                <Td className="text-right">
                  <Button size="sm" variant="ghost" aria-label={`Delete ${credential.name}`}
                          onClick={() => {
                            if (window.confirm(`Delete credential reference '${credential.name}'?`))
                              remove.mutate(credential.id)
                          }}>
                    <Trash2 className="h-3.5 w-3.5 text-red-500" />
                  </Button>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="Add credential reference"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button loading={create.isPending} onClick={() => create.mutate()}>Create</Button>
          </>
        }
      >
        {formError && <div className="mb-3"><Alert tone="danger">{formError}</Alert></div>}
        <FormRow label="Name" htmlFor="cred-name" required hint="Lowercase letters, digits, dots, dashes and slashes.">
          <Input id="cred-name" className="font-mono" placeholder="guest-local-admin"
                 value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        </FormRow>
        <FormRow label="Provider" htmlFor="cred-provider">
          <Select id="cred-provider" value={form.provider}
                  onChange={(event) => setForm({ ...form, provider: event.target.value })}>
            <option value="env">Environment (development)</option>
            <option value="vault">HashiCorp Vault (production)</option>
          </Select>
        </FormRow>
        <FormRow label="Description" htmlFor="cred-desc">
          <Input id="cred-desc" value={form.description}
                 onChange={(event) => setForm({ ...form, description: event.target.value })} />
        </FormRow>
        <Alert tone="info">
          After creating the reference, provision the actual secret in the provider — for the env provider set
         {' '}<span className="font-mono">SECRETS_{form.name ? form.name.replaceAll(/[^\w]/g, '_').toUpperCase() : 'NAME'}_USERNAME</span>{' '}
          and{' '}
          <span className="font-mono">…_PASSWORD</span>.
        </Alert>
      </Dialog>
    </div>
  )
}
