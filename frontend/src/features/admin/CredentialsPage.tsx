import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Database, KeyRound, LockKeyhole, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { FormRow, Input, Select } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { isValidSecretReference, SECRET_REFERENCE_HINT } from '@/features/admin/secret-reference'
import { api } from '@/lib/api'
import { displayValue, formatDateTime } from '@/lib/utils'
import type { SecretReferenceOut } from '@/types/api'

const PROVIDER_META = {
  env: {
    label: 'Environment provider',
    description: 'Best suited to development and controlled local environments.',
    icon: KeyRound,
  },
  vault: {
    label: 'HashiCorp Vault',
    description: 'Centralized secret resolution for production workloads.',
    icon: Database,
  },
} as const

function providerMeta(provider: string) {
  return PROVIDER_META[provider as keyof typeof PROVIDER_META] ?? {
    label: 'External secret provider',
    description: 'Secret material is managed outside InfraOps.',
    icon: LockKeyhole,
  }
}

export function CredentialsPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<SecretReferenceOut | null>(null)
  const [form, setForm] = useState({ name: '', provider: 'env', description: '' })
  const [formError, setFormError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const credentials = useQuery({ queryKey: ['admin-credentials'], queryFn: () => api.admin.credentials() })
  const normalizedReferenceName = form.name.trim()
  const referenceNameError = form.name.length > 0 && !isValidSecretReference(normalizedReferenceName)
    ? 'Enter a valid secret reference from 2 to 150 characters.'
    : null

  const create = useMutation({
    mutationFn: () => api.admin.createCredential({ ...form, name: normalizedReferenceName }),
    onSuccess: () => {
      setDialogOpen(false)
      setForm({ name: '', provider: 'env', description: '' })
      setFormError(null)
      void queryClient.invalidateQueries({ queryKey: ['admin-credentials'] })
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : 'Create failed.'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.admin.deleteCredential(id),
    onMutate: () => setActionError(null),
    onSuccess: () => {
      setActionError(null)
      setPendingDelete(null)
      void queryClient.invalidateQueries({ queryKey: ['admin-credentials'] })
    },
    onError: (error) => {
      setActionError(error instanceof Error ? error.message : 'The credential reference could not be deleted.')
      setPendingDelete(null)
    },
  })

  const items = credentials.data ?? []
  const vaultCount = items.filter((item) => item.provider === 'vault').length
  const environmentCount = items.filter((item) => item.provider === 'env').length

  function openCreateDialog() {
    setDialogOpen(true)
    setFormError(null)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Security administration"
        title="Credential references"
        description="Connect automation to approved secret providers through safe, named references. Secret values never appear in this workspace."
        actions={<Button size="sm" onClick={openCreateDialog}><Plus className="h-4 w-4" /> Add reference</Button>}
        meta={(
          <>
            <span>{items.length} registered reference{items.length === 1 ? '' : 's'}</span>
            <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5 text-brand-700" /> Provider-backed access only</span>
          </>
        )}
      />

      <section className="animate-panel-reveal overflow-hidden rounded-2xl border border-[#285f50] bg-[#173f34] text-white shadow-[0_18px_44px_rgba(23,63,52,0.18)]" aria-label="Credential security model">
        <div className="relative grid gap-4 px-5 py-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:px-6">
          <div className="pointer-events-none absolute -right-12 -top-20 h-44 w-44 rounded-full bg-[#d8f06a]/10 blur-2xl" aria-hidden />
          <div className="relative flex items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#d8f06a] text-[#173f34] shadow-[0_0_0_1px_rgba(255,255,255,0.16)]">
              <LockKeyhole className="h-5 w-5" aria-hidden />
            </span>
            <div>
              <h2 className="text-sm font-semibold tracking-[-0.01em]">References, never secret values</h2>
              <p className="mt-1 max-w-2xl text-xs leading-5 text-white/65">
                InfraOps stores only the reference name and provider. Credentials are resolved at execution time and remain in the external provider.
              </p>
            </div>
          </div>
          <div className="relative flex flex-wrap gap-2 sm:justify-end">
            <Badge className="bg-white/10 text-white ring-white/15">{vaultCount} Vault</Badge>
            <Badge className="bg-[#d8f06a]/12 text-[#e9f8a6] ring-[#d8f06a]/25">{environmentCount} environment</Badge>
          </div>
        </div>
      </section>

      {actionError && <Alert tone="danger" title="Credential action could not be completed">{actionError}</Alert>}

      {credentials.isLoading ? (
        <LoadingState title="Loading credential references" description="Retrieving safe reference names from the provider registry." />
      ) : credentials.isError ? (
        <Alert tone="danger" title="Secret reference registry unavailable">
          Credential references could not be loaded.{' '}
          <Button size="sm" variant="secondary" onClick={() => void credentials.refetch()}>Retry</Button>
        </Alert>
      ) : items.length === 0 ? (
        <EmptyState
          title="No credential references"
          description="Register a safe provider reference so automation can resolve credentials without displaying secret material."
          action={<Button size="sm" onClick={openCreateDialog}><Plus className="h-3.5 w-3.5" /> Add reference</Button>}
        />
      ) : (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-label="Registered credential references">
          {items.map((credential) => {
            const provider = providerMeta(credential.provider)
            const ProviderIcon = provider.icon
            return (
              <article
                key={credential.id}
                className="group animate-row-enter overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)] transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-[0_18px_42px_rgba(23,79,64,0.1)]"
              >
                <div className="h-1 bg-gradient-to-r from-brand-700 via-brand-500 to-[#d8f06a]" aria-hidden />
                <div className="flex min-h-56 flex-col p-5">
                  <div className="flex items-start gap-3">
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-100 transition-colors group-hover:bg-brand-100">
                      <ProviderIcon className="h-[18px] w-[18px]" aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <h2 className="break-words text-base font-semibold tracking-[-0.02em] text-[#1c2520]">{credential.name}</h2>
                      <Badge tone="info" className="mt-2">{provider.label}</Badge>
                    </div>
                  </div>

                  <p className="mt-4 text-xs leading-5 text-[#68736d]">
                    {displayValue(credential.description, 'No description has been added for this reference.')}
                  </p>

                  <div className="mt-4 rounded-xl border border-[#e1e7df] bg-[#f7faf5] px-3.5 py-3">
                    <p className="flex items-center gap-2 text-[11px] font-semibold text-[#355047]"><ShieldCheck className="h-3.5 w-3.5 text-brand-700" /> Secret values stay in the provider</p>
                    <p className="mt-1 text-[10px] leading-4 text-[#78837c]">{provider.description}</p>
                  </div>

                  <footer className="mt-auto flex items-end justify-between gap-3 border-t border-[#e7eae6] pt-4">
                    <div className="min-w-0">
                      <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.09em] text-[#89928c]"><CalendarClock className="h-3 w-3" /> Registered</p>
                      <p className="mt-1 truncate text-[11px] font-medium text-[#59635d]">{formatDateTime(credential.created_at)}</p>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="shrink-0 text-red-700 hover:bg-red-50 hover:text-red-800"
                      aria-label={`Delete ${credential.name}`}
                      onClick={() => setPendingDelete(credential)}
                    >
                      <Trash2 className="h-3.5 w-3.5" /> Remove
                    </Button>
                  </footer>
                </div>
              </article>
            )
          })}
        </section>
      )}

      <Dialog
        open={dialogOpen}
        onClose={() => { if (!create.isPending) setDialogOpen(false) }}
        title="Add credential reference"
        footer={(
          <>
            <Button variant="secondary" disabled={create.isPending} onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button loading={create.isPending} disabled={!isValidSecretReference(normalizedReferenceName)} onClick={() => create.mutate()}>Create reference</Button>
          </>
        )}
      >
        {formError && <div className="mb-4"><Alert tone="danger">{formError}</Alert></div>}
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-brand-100 bg-brand-50/60 p-3.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white text-brand-700 ring-1 ring-inset ring-brand-100"><KeyRound className="h-4 w-4" /></span>
          <div><p className="text-xs font-semibold text-brand-900">Create a safe lookup name</p><p className="mt-1 text-[11px] leading-4 text-brand-800/70">This registry stores the reference only. No username, password, token, or secret value is entered here.</p></div>
        </div>
        <FormRow label="Reference name" htmlFor="cred-name" required hint={SECRET_REFERENCE_HINT} error={referenceNameError ?? undefined}>
          <Input
            id="cred-name"
            placeholder="guest-local-admin"
            maxLength={150}
            autoComplete="off"
            value={form.name}
            onChange={(event) => { setFormError(null); setForm({ ...form, name: event.target.value.toLowerCase() }) }}
          />
        </FormRow>
        <FormRow label="Secret provider" htmlFor="cred-provider">
          <Select
            id="cred-provider"
            value={form.provider}
            onChange={(event) => { setFormError(null); setForm({ ...form, provider: event.target.value }) }}
          >
            <option value="env">Environment provider (development)</option>
            <option value="vault">HashiCorp Vault (production)</option>
          </Select>
        </FormRow>
        <FormRow label="Description" htmlFor="cred-desc" hint="Explain which workflow or operator group should use this reference.">
          <Input
            id="cred-desc"
            placeholder="Local administrator for Windows deployment"
            value={form.description}
            onChange={(event) => { setFormError(null); setForm({ ...form, description: event.target.value }) }}
          />
        </FormRow>
        <Alert tone="info" title="Complete provider setup separately">
          After registering this name, provision the corresponding credential fields in the selected provider according to your organization&apos;s naming policy.
        </Alert>
      </Dialog>

      <Dialog
        open={Boolean(pendingDelete)}
        onClose={() => { if (!remove.isPending) setPendingDelete(null) }}
        title="Remove this credential reference?"
        footer={(
          <>
            <Button variant="secondary" disabled={remove.isPending} onClick={() => setPendingDelete(null)}>Keep reference</Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => pendingDelete && remove.mutate(pendingDelete.id)}>
              <Trash2 className="h-4 w-4" /> Remove reference
            </Button>
          </>
        )}
      >
        <div className="space-y-4">
          <Alert tone="warning" title="Automation may depend on this reference">
            Existing deployment requests that use this name may no longer be able to resolve their credentials.
          </Alert>
          <div className="rounded-xl border border-[#dfe4de] bg-[#f8f9f6] p-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">Reference</p>
            <p className="mt-1 break-words text-sm font-semibold text-[#202923]">{pendingDelete?.name}</p>
            <p className="mt-1 text-xs text-[#68736d]">{pendingDelete ? providerMeta(pendingDelete.provider).label : ''}</p>
          </div>
        </div>
      </Dialog>
    </div>
  )
}
