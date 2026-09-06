import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  CloudCog,
  KeyRound,
  LockKeyhole,
  Pencil,
  PlugZap,
  Plus,
  Server,
  ShieldCheck,
  ShieldOff,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { api } from '@/lib/api'
import { displayValue, formatDateTime, humanizeIdentifier } from '@/lib/utils'
import type { VCenterConnectionAdminOut } from '@/types/api'

const EMPTY_FORM = {
  name: '',
  host: '',
  port: 443,
  credential_secret_ref: '',
  verify_ssl: true,
  notes: '',
}

const HOSTNAME_PATTERN = /^[a-zA-Z0-9](?:[a-zA-Z0-9.\-]{0,251}[a-zA-Z0-9])?$/

function credentialBase(connection: VCenterConnectionAdminOut): string {
  const usernameBase = connection.username_secret_ref.replace(/\/username$/, '')
  const passwordBase = connection.password_secret_ref.replace(/\/password$/, '')
  return usernameBase === passwordBase ? usernameBase : ''
}

interface TestFeedback {
  ok: boolean
  message: string
}

function connectionStateMeta(state: string) {
  switch (state.toLowerCase()) {
    case 'connected':
      return {
        icon: CheckCircle2,
        tone: 'success' as const,
        iconClass: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
        railClass: 'bg-emerald-500',
      }
    case 'error':
      return {
        icon: CircleAlert,
        tone: 'danger' as const,
        iconClass: 'bg-red-50 text-red-700 ring-red-200',
        railClass: 'bg-red-500',
      }
    default:
      return {
        icon: CloudCog,
        tone: 'neutral' as const,
        iconClass: 'bg-[#eef2ed] text-[#647068] ring-[#dae1da]',
        railClass: 'bg-[#aab3ad]',
      }
  }
}

export function VCenterConnectionsPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<VCenterConnectionAdminOut | null>(null)
  const [pendingDelete, setPendingDelete] = useState<VCenterConnectionAdminOut | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, TestFeedback>>({})

  const connections = useQuery({ queryKey: ['admin-vcenters'], queryFn: () => api.admin.vcenters() })
  const credentials = useQuery({
    queryKey: ['admin-credentials'],
    queryFn: () => api.admin.credentials(),
    refetchInterval: 5_000,
    staleTime: 0,
  })
  const vcenterCredentials = (credentials.data ?? []).filter(
    (credential) => credential.purpose === 'vcenter' && credential.configured,
  )

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
      credential_secret_ref: credentialBase(connection),
      verify_ssl: connection.verify_ssl,
      notes: connection.notes,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  function openDelete(connection: VCenterConnectionAdminOut) {
    setActionError(null)
    setPendingDelete(connection)
  }

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        ...form,
        name: form.name.trim(),
        host: form.host.trim(),
        port: Number(form.port),
        username_secret_ref: `${form.credential_secret_ref}/username`,
        password_secret_ref: `${form.credential_secret_ref}/password`,
      }
      if (editing) return api.admin.updateVCenter(editing.id, body)
      return api.admin.createVCenter(body)
    },
    onMutate: () => setFormError(null),
    onSuccess: () => {
      setDialogOpen(false)
      setEditing(null)
      void queryClient.invalidateQueries({ queryKey: ['admin-vcenters'] })
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : 'The vCenter connection could not be saved.'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.admin.deleteVCenter(id),
    onMutate: () => setActionError(null),
    onSuccess: () => {
      setActionError(null)
      setPendingDelete(null)
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
        [id]: {
          ok: result.ok,
          message: result.ok && result.latency_ms != null
            ? `${result.detail} Response time: ${result.latency_ms} ms.`
            : result.detail,
        },
      }))
      void queryClient.invalidateQueries({ queryKey: ['admin-vcenters'] })
    },
    onError: (error) => setActionError(error instanceof Error ? error.message : 'The connection test could not be completed.'),
  })

  const items = connections.data ?? []
  const connectedCount = items.filter((item) => item.connection_state === 'connected').length
  const attentionCount = items.filter((item) => item.connection_state === 'error').length
  const tlsCount = items.filter((item) => item.verify_ssl).length
  const normalizedName = form.name.trim()
  const normalizedHost = form.host.trim()
  const nameError = normalizedName.length < 2 || normalizedName.length > 150
    ? 'Use a connection name from 2 to 150 characters.'
    : null
  const hostError = normalizedHost.length < 3
    || normalizedHost.length > 255
    || !HOSTNAME_PATTERN.test(normalizedHost)
    ? 'Enter a valid DNS hostname or IP address from 3 to 255 characters.'
    : null
  const credentialError = !form.credential_secret_ref
    ? 'Select a configured vCenter service account.'
    : null
  const validPort = Number.isInteger(form.port) && form.port >= 1 && form.port <= 65535
  const formValidationErrors = [
    nameError,
    hostError,
    !validPort ? 'Enter a whole-number port from 1 to 65,535.' : null,
    credentialError,
  ].filter((error): error is string => Boolean(error))
  const formIsValid = formValidationErrors.length === 0

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Infrastructure administration"
        title="vCenter connections"
        description="Manage vSphere endpoints, encrypted credentials, certificate verification, and connection health in one place."
        actions={<Button size="sm" onClick={openCreate}><Plus className="h-4 w-4" /> Add vCenter</Button>}
        meta={<><span>{items.length} configured endpoint{items.length === 1 ? '' : 's'}</span><span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5 text-brand-700" /> Credentials encrypted in the backend</span></>}
      />

      <section className="animate-panel-reveal overflow-hidden rounded-2xl border border-[#285f50] bg-[#173f34] text-white shadow-[0_18px_44px_rgba(23,63,52,0.18)]" aria-label="vCenter connection summary">
        <div className="relative grid gap-5 px-5 py-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:px-6">
          <span className="pointer-events-none absolute -right-12 -top-20 h-44 w-44 rounded-full bg-[#d8f06a]/10 blur-2xl" aria-hidden />
          <div className="relative flex items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#d8f06a] text-[#173f34]"><CloudCog className="h-5 w-5" /></span>
            <div><h2 className="text-sm font-semibold">Infrastructure control plane</h2><p className="mt-1 max-w-xl text-xs leading-5 text-white/65">Connection tests authenticate through external references and update the latest health status without displaying credential values.</p></div>
          </div>
          <div className="relative grid grid-cols-3 gap-2 text-center">
            <div className="rounded-xl bg-white/[0.07] px-3 py-2 ring-1 ring-inset ring-white/10"><p className="text-lg font-semibold tabular-nums">{connectedCount}</p><p className="text-[9px] uppercase tracking-[0.1em] text-white/50">Connected</p></div>
            <div className="rounded-xl bg-white/[0.07] px-3 py-2 ring-1 ring-inset ring-white/10"><p className="text-lg font-semibold tabular-nums">{attentionCount}</p><p className="text-[9px] uppercase tracking-[0.1em] text-white/50">Attention</p></div>
            <div className="rounded-xl bg-[#d8f06a]/10 px-3 py-2 ring-1 ring-inset ring-[#d8f06a]/20"><p className="text-lg font-semibold tabular-nums text-[#e9f8a6]">{tlsCount}</p><p className="text-[9px] uppercase tracking-[0.1em] text-white/50">TLS verified</p></div>
          </div>
        </div>
      </section>

      {actionError && !pendingDelete && <Alert tone="danger" title="vCenter action could not be completed">{actionError}</Alert>}

      {connections.isLoading ? (
        <LoadingState title="Loading vCenter connections" description="Checking configured endpoints and their latest health state." />
      ) : connections.isError ? (
        <Alert tone="danger" title="vCenter registry unavailable">The connection registry could not be loaded. <Button size="sm" variant="secondary" onClick={() => void connections.refetch()}>Retry</Button></Alert>
      ) : items.length === 0 ? (
        <EmptyState title="No vCenter endpoints configured" description="Add a connection before operators can browse inventory or provision virtual machines." action={<Button size="sm" onClick={openCreate}><Plus className="h-3.5 w-3.5" /> Add vCenter</Button>} />
      ) : (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-label="Configured vCenter connections">
          {items.map((connection) => {
            const status = connectionStateMeta(connection.connection_state)
            const StatusIcon = status.icon
            const feedback = testResults[connection.id]
            return (
              <article key={connection.id} className="group animate-row-enter overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)] transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-[0_18px_42px_rgba(23,79,64,0.1)]">
                <div className={`h-1 ${status.railClass}`} aria-hidden />
                <div className="flex min-h-[330px] flex-col p-5">
                  <header className="flex items-start gap-3">
                    <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ring-1 ring-inset ${status.iconClass}`}><StatusIcon className="h-[18px] w-[18px]" /></span>
                    <div className="min-w-0 flex-1">
                      <h2 className="break-words text-base font-semibold tracking-[-0.02em] text-[#1c2520]">{connection.name}</h2>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <Badge tone={status.tone}><StatusIcon className="h-3 w-3" /> {humanizeIdentifier(connection.connection_state)}</Badge>
                        {!connection.enabled && <Badge tone="neutral">Disabled</Badge>}
                      </div>
                    </div>
                  </header>

                  <dl className="mt-4 grid grid-cols-2 gap-2">
                    <div className="col-span-2 rounded-xl border border-[#e1e7df] bg-[#f7faf5] px-3.5 py-3"><dt className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-[0.1em] text-[#7b857f]"><Server className="h-3 w-3" /> Server</dt><dd className="mt-1.5 break-all text-xs font-semibold text-[#344139]">{connection.host}</dd></div>
                    <div className="rounded-xl border border-[#e1e7df] bg-white px-3 py-2.5"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">Port</dt><dd className="mt-1 text-xs font-semibold tabular-nums text-[#344139]">{connection.port}</dd></div>
                    <div className="rounded-xl border border-[#e1e7df] bg-white px-3 py-2.5"><dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">Certificate</dt><dd className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-[#344139]">{connection.verify_ssl ? <ShieldCheck className="h-3.5 w-3.5 text-emerald-700" /> : <ShieldOff className="h-3.5 w-3.5 text-amber-700" />}{connection.verify_ssl ? 'Verified' : 'Not verified'}</dd></div>
                  </dl>

                  <div className="mt-3 rounded-xl bg-[#f7f8f5] px-3.5 py-3">
                    <p className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-[0.1em] text-[#7b857f]"><CalendarClock className="h-3 w-3" /> Last checked</p>
                    <p className="mt-1 text-[11px] font-medium text-[#536058]">{formatDateTime(connection.last_checked_at)}</p>
                    {connection.notes && <p className="mt-2 line-clamp-2 border-t border-[#e4e8e3] pt-2 text-[10px] leading-4 text-[#758079]">{displayValue(connection.notes)}</p>}
                  </div>

                  {feedback && <div className="mt-3"><Alert tone={feedback.ok ? 'success' : 'danger'} title={feedback.ok ? 'Connection verified' : 'Connection test failed'}>{feedback.message}</Alert></div>}
                  {!feedback && connection.connection_state === 'error' && connection.last_connection_error && <div className="mt-3"><Alert tone="warning" title="Latest check needs attention">{connection.last_connection_error}</Alert></div>}

                  <footer className="mt-auto flex flex-wrap items-center gap-1.5 border-t border-[#e7eae6] pt-4">
                    <Button size="sm" variant="secondary" loading={test.isPending && test.variables === connection.id} onClick={() => test.mutate(connection.id)}><PlugZap className="h-3.5 w-3.5" /> Test connection</Button>
                    <Button size="sm" variant="ghost" onClick={() => openEdit(connection)} aria-label={`Edit ${connection.name}`}><Pencil className="h-3.5 w-3.5" /> Edit</Button>
                    <Button size="sm" variant="ghost" className="ml-auto text-red-700 hover:bg-red-50 hover:text-red-800" aria-label={`Remove ${connection.name}`} onClick={() => openDelete(connection)}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </footer>
                </div>
              </article>
            )
          })}
        </section>
      )}

      <Dialog
        open={dialogOpen}
        onClose={() => { if (!save.isPending) setDialogOpen(false) }}
        title={editing ? `Edit ${editing.name}` : 'Add vCenter connection'}
        footer={<><Button variant="secondary" disabled={save.isPending} onClick={() => setDialogOpen(false)}>Cancel</Button><Button disabled={!formIsValid} loading={save.isPending} onClick={() => save.mutate()}>{editing ? 'Save changes' : 'Add connection'}</Button></>}
      >
        {formError && <div className="mb-4"><Alert tone="danger">{formError}</Alert></div>}
        {!formIsValid && (
          <div className="mb-4">
            <Alert tone="info" title="Complete the required connection details">
              <ul className="list-disc space-y-0.5 pl-4">{formValidationErrors.map((error) => <li key={error}>{error}</li>)}</ul>
            </Alert>
          </div>
        )}
        <section className="mb-5 rounded-xl border border-[#dfe5df] bg-[#f8faf6] p-4">
          <div className="mb-4 flex items-start gap-3"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white text-brand-700 ring-1 ring-inset ring-brand-100"><Server className="h-4 w-4" /></span><div><h3 className="text-xs font-semibold text-[#26312b]">Endpoint details</h3><p className="mt-1 text-[11px] leading-4 text-[#758079]">Use the hostname that InfraOps can reach from its backend service.</p></div></div>
          <FormRow label="Connection name" htmlFor="vc-name" required hint="A recognizable label for operators, such as Production vCenter." error={form.name.length > 0 ? nameError ?? undefined : undefined}><Input id="vc-name" maxLength={150} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></FormRow>
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_100px]">
            <FormRow label="Server hostname" htmlFor="vc-host" required hint="DNS name or reachable IP address." error={form.host.length > 0 ? hostError ?? undefined : undefined}><Input id="vc-host" maxLength={255} value={form.host} onChange={(event) => setForm({ ...form, host: event.target.value })} /></FormRow>
            <FormRow label="Port" htmlFor="vc-port" hint="Usually 443." error={!validPort ? 'Use a whole number from 1 to 65,535.' : undefined}><Input id="vc-port" type="number" min={1} max={65535} value={Number.isFinite(form.port) ? form.port : ''} aria-invalid={!validPort} onChange={(event) => setForm({ ...form, port: event.target.value === '' ? Number.NaN : Number(event.target.value) })} /></FormRow>
          </div>
          <Checkbox label={<span><span className="font-medium">Verify the TLS certificate</span><span className="mt-0.5 block text-[11px] text-[#758079]">Recommended for every production endpoint.</span></span>} checked={form.verify_ssl} onChange={(event) => setForm({ ...form, verify_ssl: event.target.checked })} />
          {!form.verify_ssl && <div className="mt-3"><Alert tone="warning" title="Certificate verification is off">The server identity will not be verified during the connection.</Alert></div>}
        </section>

        <section className="mb-5 rounded-xl border border-brand-100 bg-brand-50/50 p-4">
          <div className="mb-4 flex items-start gap-3"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white text-brand-700 ring-1 ring-inset ring-brand-100"><LockKeyhole className="h-4 w-4" /></span><div><h3 className="text-xs font-semibold text-brand-900">Managed credential</h3><p className="mt-1 text-[11px] leading-4 text-brand-800/70">Select an encrypted service account from Administration → Credentials.</p></div></div>
          <FormRow label="vCenter service account" htmlFor="vc-credential" required error={credentialError ?? undefined}>
            <Select id="vc-credential" value={form.credential_secret_ref} onChange={(event) => setForm({ ...form, credential_secret_ref: event.target.value })}>
              <option value="">Select service account</option>
              {vcenterCredentials.map((credential) => <option key={credential.name} value={credential.name}>{credential.name} · revision {credential.revision}</option>)}
            </Select>
          </FormRow>
          <p className="flex items-center gap-1.5 text-[10px] text-brand-800/70"><KeyRound className="h-3.5 w-3.5" /> Credential rotations apply to the next connection without restarting services.</p>
        </section>

        <FormRow label="Operator notes" htmlFor="vc-notes" hint="Optional context such as environment ownership or maintenance window."><Input id="vc-notes" placeholder="Managed by the virtualization team" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></FormRow>
      </Dialog>

      <Dialog
        open={Boolean(pendingDelete)}
        onClose={() => { if (!remove.isPending) setPendingDelete(null) }}
        title="Remove this vCenter connection?"
        footer={<><Button variant="secondary" disabled={remove.isPending} onClick={() => setPendingDelete(null)}>Keep connection</Button><Button variant="danger" loading={remove.isPending} onClick={() => pendingDelete && remove.mutate(pendingDelete.id)}><Trash2 className="h-4 w-4" /> Remove connection</Button></>}
      >
        <div className="space-y-4">
          {actionError && <Alert tone="danger" title="Connection was not removed">{actionError}</Alert>}
          <Alert tone="warning" title="Inventory access may be interrupted">Operators will no longer be able to browse or provision against this endpoint after it is removed.</Alert>
          <div className="rounded-xl border border-[#dfe4de] bg-[#f8f9f6] p-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">Connection</p>
            <p className="mt-1 break-words text-sm font-semibold text-[#202923]">{pendingDelete?.name}</p>
            <p className="mt-1 break-all text-xs text-[#68736d]">{pendingDelete ? `${pendingDelete.host}:${pendingDelete.port}` : ''}</p>
          </div>
        </div>
      </Dialog>
    </div>
  )
}
