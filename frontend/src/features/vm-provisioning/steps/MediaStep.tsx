import { Check, Disc3, FileArchive, HardDrive, MapPin, RefreshCw, Search, Unplug } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { Input } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useIsos, useTemplates } from '@/features/vm-provisioning/hooks'
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

export function MediaStep() {
  const wizard = useWizard()
  const data = wizard.data
  const [search, setSearch] = useState('')
  const isPackage = data.source_type === 'template'
  const templates = useTemplates(data.vcenter_id, data.datacenter_id, isPackage)
  const isos = useIsos(data.vcenter_id, data.datacenter_id, !isPackage)

  useEffect(() => {
    if (
      isPackage
      && templates.isSuccess
      && data.template_id
      && !templates.data.some((item) => item.id === data.template_id)
    ) {
      wizard.update({ template_id: '' })
    }
  }, [data.template_id, isPackage, templates.data, templates.isSuccess, wizard.update])

  useEffect(() => {
    if (
      !isPackage
      && isos.isSuccess
      && data.iso_id
      && !isos.data.some((item) => item.id === data.iso_id)
    ) {
      wizard.update({ iso_id: null })
    }
  }, [data.iso_id, isPackage, isos.data, isos.isSuccess, wizard.update])

  const filteredTemplates = useMemo(() => {
    const query = search.trim().toLowerCase()
    const items = templates.data ?? []
    if (!query) return items
    return items.filter((item) => [item.name, item.type, item.description, item.storage_name, item.location]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(query))
  }, [search, templates.data])

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
    <section aria-label="Deployment source" className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="console-kicker">Step 3 · Source</p>
          <h2>{isPackage ? 'Choose an OVF or OVA package' : 'Choose installation media'}</h2>
          <p>
            {isPackage
              ? 'Choose an actual OVF or OVA package from the selected vCenter library. The target datacenter is validated again before deployment.'
              : 'Only ISO images stored in the selected datacenter are available. You may also create the VM without mounted media.'}
          </p>
        </div>
        <Badge tone="info"><MapPin className="h-3 w-3" /> {isPackage ? 'Selected deployment target' : 'Selected datacenter only'}</Badge>
      </header>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#87908a]" aria-hidden />
          <Input
            aria-label={`Search ${isPackage ? 'packages' : 'ISO images'}`}
            className="pl-9"
            placeholder={`Search ${isPackage ? 'OVF / OVA packages' : 'ISO images'}`}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <p className="text-xs text-[#68736d]">
          {isPackage ? filteredTemplates.length : filteredIsos.length} result{(isPackage ? filteredTemplates.length : filteredIsos.length) === 1 ? '' : 's'}
        </p>
      </div>

      {isPackage ? (
        templates.isLoading ? (
          <LoadingState title="Loading OVF and OVA packages" description="Retrieving deployment packages from the selected vCenter library." />
        ) : templates.isError ? (
          <EmptyState
            title="Packages could not be loaded"
            description={templates.error instanceof Error ? templates.error.message : 'Try the inventory request again.'}
            action={<Button type="button" variant="secondary" onClick={() => void templates.refetch()}><RefreshCw className="h-4 w-4" /> Try again</Button>}
          />
        ) : (templates.data ?? []).length === 0 ? (
          <EmptyState
            title="No OVF or OVA packages are available for this target"
            description="Publish a deployable package to the vCenter content library or choose another target. Classic VM templates are intentionally not listed here."
            action={<Button type="button" variant="secondary" onClick={() => void templates.refetch()}><RefreshCw className="h-4 w-4" /> Refresh inventory</Button>}
          />
        ) : filteredTemplates.length === 0 ? (
          <EmptyState title="No packages match your search" description="Clear the search to see every available OVF and OVA package." />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2" role="radiogroup" aria-label="OVF and OVA packages">
            {filteredTemplates.map((item) => {
              const selected = data.template_id === item.id
              return (
                <button
                  key={item.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => wizard.update({ template_id: item.id })}
                  className={cn(
                    'group flex min-h-44 flex-col rounded-2xl border bg-white p-5 text-left transition-[border-color,box-shadow,transform]',
                    selected
                      ? 'border-brand-600 shadow-[0_0_0_2px_rgba(31,109,88,0.12),0_18px_38px_rgba(23,79,64,0.09)]'
                      : 'border-[#d8ddd7] hover:-translate-y-0.5 hover:border-[#aeb9b1] hover:shadow-[var(--ui-shadow)]',
                  )}
                >
                  <div className="flex items-start gap-3">
                    <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#edf7f3] text-brand-700">
                      <FileArchive className="h-5 w-5" aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-semibold text-[#1e2722]">{item.name}</span>
                        <Badge tone="info">{item.type}</Badge>
                      </div>
                      {item.description && <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-[#68736d]">{item.description}</p>}
                    </div>
                    <ChoiceMark selected={selected} />
                  </div>
                  <div className="mt-auto grid grid-cols-2 gap-3 border-t border-[#e5e8e4] pt-4 text-xs text-[#59635d]">
                    <span className="flex min-w-0 items-center gap-2"><HardDrive className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{item.storage_name ?? 'Storage not available'}</span></span>
                    <span className="text-right font-medium">{formatBytes(item.size_bytes)}</span>
                    {item.location && <span className="col-span-2 truncate text-[11px] text-[#7b857f]" title={item.location}>{item.location}</span>}
                  </div>
                </button>
              )
            })}
          </div>
        )
      ) : (
        <div className="space-y-3">
          <button
            type="button"
            role="radio"
            aria-checked={data.iso_id === null}
            onClick={() => wizard.update({ iso_id: null })}
            className={cn(
              'flex w-full items-center gap-4 rounded-2xl border bg-white p-5 text-left',
              data.iso_id === null ? 'border-brand-600 shadow-[0_0_0_2px_rgba(31,109,88,0.12)]' : 'border-[#d8ddd7] hover:border-[#aeb9b1]',
            )}
          >
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#f1f3ef] text-[#65706a]"><Unplug className="h-5 w-5" /></span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-[#1e2722]">No ISO</span>
              <span className="mt-1 block text-xs text-[#68736d]">Create the VM with an empty optical drive and mount installation media later.</span>
            </span>
            <ChoiceMark selected={data.iso_id === null} />
          </button>

          {isos.isLoading ? (
            <LoadingState title="Loading ISO images" description="Searching storage in the selected datacenter." />
          ) : isos.isError ? (
            <EmptyState
              title="ISO images could not be loaded"
              description={isos.error instanceof Error ? isos.error.message : 'You can retry or continue with no ISO.'}
              action={<Button type="button" variant="secondary" onClick={() => void isos.refetch()}><RefreshCw className="h-4 w-4" /> Try again</Button>}
            />
          ) : (isos.data ?? []).length === 0 ? (
            <EmptyState title="No ISO images are available in this datacenter" description="Continue with no ISO or upload installation media to an accessible datastore." />
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
        </div>
      )}

      {wizard.errors.template_id && <p className="text-xs font-medium text-red-700" role="alert">{wizard.errors.template_id}</p>}
    </section>
  )
}
