import { Check, Disc3, KeyRound, MapPin, RefreshCw, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { FormRow, Input, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useIsos, useProvisioningCredentials } from '@/features/vm-provisioning/hooks'
import { cn, formatBytes, formatDateTime } from '@/lib/utils'

function ChoiceMark({ selected }: { selected: boolean }) {
  return (
    <span className={cn(
      'grid h-6 w-6 shrink-0 place-items-center rounded-full border',
      selected ? 'border-brand-700 bg-brand-700 text-white' : 'border-[#cbd2cc] bg-white text-transparent',
    )}>
      <Check className="h-3.5 w-3.5" aria-hidden />
    </span>
  )
}

function MediaInventoryLoading() {
  return (
    <div className="overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white" role="status" aria-live="polite">
      <div className="flex items-center gap-3 border-b border-[#e2e6e1] bg-[#f8faf6] px-5 py-4">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand-50 text-brand-700">
          <Spinner className="h-5 w-5 text-brand-700" />
        </span>
        <div>
          <p className="text-sm font-semibold text-[#202923]">Loading ISO images</p>
          <p className="mt-1 text-xs text-[#68736d]">Scanning accessible datastores for Windows Server installation media.</p>
        </div>
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2" aria-hidden>
        {[0, 1, 2, 3].map((item) => (
          <div key={item} className="rounded-xl border border-[#e3e7e2] p-4">
            <div className="loading-sheen h-3 w-2/3 rounded-full" />
            <div className="loading-sheen mt-3 h-2.5 w-2/5 rounded-full" />
            <div className="loading-sheen mt-5 h-2 w-full rounded-full" />
          </div>
        ))}
      </div>
    </div>
  )
}

function ProductKeySelector() {
  const wizard = useWizard()
  const keys = useProvisioningCredentials('windows_product_key')
  const items = keys.data ?? []
  const selected = wizard.data.product_key_secret_ref

  useEffect(() => {
    if (keys.isSuccess && selected && !items.some((key) => key.name === selected)) {
      wizard.update({ product_key_secret_ref: '' })
    }
  }, [items, keys.isSuccess, selected, wizard.update])

  return (
    <div className="console-group">
      <div className="console-group-header">
        <div>
          <p className="console-group-title">Product key</p>
          <p className="console-group-description">
            Volume-license and evaluation media install without one. Retail and MAK media need the key an administrator stored.
          </p>
        </div>
        <KeyRound className="h-4 w-4 text-slate-400" />
      </div>
      <div className="console-group-body">
        <FormRow label="Windows product key" htmlFor="product-key" error={wizard.errors.product_key_secret_ref}>
          <Select
            id="product-key"
            value={selected}
            disabled={keys.isLoading}
            onChange={(event) => wizard.update({ product_key_secret_ref: event.target.value })}
          >
            <option value="">No product key (volume-license or evaluation media)</option>
            {items.map((key) => (
              <option key={key.name} value={key.name}>
                {key.name} · revision {key.revision}
              </option>
            ))}
          </Select>
        </FormRow>
        {keys.isError && (
          <Alert tone="warning" title="Product keys unavailable">
            The key catalog could not be loaded.{' '}
            <Button size="sm" variant="secondary" onClick={() => void keys.refetch()}>Retry</Button>
          </Alert>
        )}
      </div>
    </div>
  )
}

export function MediaStep() {
  const wizard = useWizard()
  const data = wizard.data
  const [search, setSearch] = useState('')
  const isos = useIsos(data.vcenter_id, data.datacenter_id)

  useEffect(() => {
    if (isos.isSuccess && data.iso_id && !isos.data.some((item) => item.id === data.iso_id)) {
      wizard.update({ iso_id: '' })
    }
  }, [data.iso_id, isos.data, isos.isSuccess, wizard.update])

  const filteredIsos = useMemo(() => {
    const query = search.trim().toLowerCase()
    const items = isos.data ?? []
    if (!query) return items
    return items.filter((item) => [item.name, item.datastore_name, item.path]
      .join(' ')
      .toLowerCase()
      .includes(query))
  }, [search, isos.data])

  return (
    <section aria-label="Windows installation media" className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="console-kicker">Step 2 · Windows media</p>
          <h2>Choose the Windows Server ISO</h2>
          <p>
            InfraOps boots the new VM from this ISO and installs Windows Server unattended. Only Windows Server media
            is accepted; it is checked again when you submit.
          </p>
        </div>
        <Badge tone="info"><MapPin className="h-3 w-3" /> Selected datacenter only</Badge>
      </header>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#87908a]" aria-hidden />
          <Input
            aria-label="Search ISO images"
            className="pl-9"
            placeholder="Search ISO images"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <p className="text-xs text-[#68736d]">
          {filteredIsos.length} result{filteredIsos.length === 1 ? '' : 's'}
        </p>
      </div>

      {isos.isLoading ? (
        <MediaInventoryLoading />
      ) : isos.isError ? (
        <EmptyState
          title="ISO images could not be loaded"
          description={isos.error instanceof Error ? isos.error.message : 'Retry the inventory request.'}
          action={<Button type="button" variant="secondary" onClick={() => void isos.refetch()}><RefreshCw className="h-4 w-4" /> Try again</Button>}
        />
      ) : (isos.data ?? []).length === 0 ? (
        <EmptyState
          title="No ISO images are available in this datacenter"
          description="Upload a Windows Server ISO to a datastore of this datacenter, then refresh the inventory."
          action={<Button type="button" variant="secondary" onClick={() => void isos.refetch()}><RefreshCw className="h-4 w-4" /> Refresh inventory</Button>}
        />
      ) : filteredIsos.length === 0 ? (
        <EmptyState title="No ISO images match your search" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2" role="radiogroup" aria-label="ISO images">
          {filteredIsos.map((item) => {
            const selected = data.iso_id === item.id
            return (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => wizard.update({ iso_id: item.id })}
                className={cn(
                  'flex min-h-32 items-start gap-3 rounded-2xl border bg-white p-5 text-left transition-[border-color,box-shadow,transform]',
                  selected ? 'border-brand-600 shadow-[0_0_0_2px_rgba(31,109,88,0.12)]' : 'border-[#d8ddd7] hover:-translate-y-0.5 hover:border-[#aeb9b1]',
                )}
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#fff5ed] text-accent-700"><Disc3 className="h-5 w-5" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-[#1e2722]">{item.name}</span>
                  <span className="mt-1 block truncate text-xs text-[#68736d]">{item.datastore_name}</span>
                  <span className="mt-3 block text-[11px] text-[#7b857f]">{formatBytes(item.size_bytes)} · Updated {formatDateTime(item.last_modified)}</span>
                </span>
                <ChoiceMark selected={selected} />
              </button>
            )
          })}
        </div>
      )}

      {wizard.errors.iso_id && <p className="text-xs font-medium text-red-700" role="alert">{wizard.errors.iso_id}</p>}

      <ProductKeySelector />
    </section>
  )
}
