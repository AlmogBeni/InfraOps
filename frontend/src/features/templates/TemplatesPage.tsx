import { ArrowRight, Building2, FileArchive, HardDrive, RefreshCw, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { Input, Select } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { useDatacenters, useTemplates, useVcenters } from '@/features/vm-provisioning/hooks'
import { formatBytes, formatDateTime } from '@/lib/utils'

export function TemplatesPage() {
  const vcenters = useVcenters()
  const [vcenterId, setVcenterId] = useState('')
  const [datacenterId, setDatacenterId] = useState('')
  const [search, setSearch] = useState('')
  const datacenters = useDatacenters(vcenterId)
  const templates = useTemplates(vcenterId, datacenterId)

  const items = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return templates.data ?? []
    return (templates.data ?? []).filter((item) => [item.name, item.type, item.description, item.storage_name, item.location]
      .filter(Boolean).join(' ').toLowerCase().includes(query))
  }, [search, templates.data])

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Deployment library"
        title="OVF & OVA packages"
        description="Browse actual virtual appliance packages from vCenter content libraries and choose the datacenter where a package should be deployed. Classic VM templates are not included."
        actions={<Button variant="secondary" size="sm" onClick={() => void templates.refetch()} disabled={!datacenterId}><RefreshCw className="h-3.5 w-3.5" /> Refresh</Button>}
      />

      <section className="rounded-2xl border border-[#d8ddd7] bg-white p-4 shadow-[var(--ui-shadow)]" aria-label="Template filters">
        <div className="grid gap-3 md:grid-cols-[minmax(180px,0.8fr)_minmax(180px,0.8fr)_minmax(240px,1.4fr)_auto] md:items-end">
          <div>
            <label className="field-label" htmlFor="template-vcenter">vCenter</label>
            <Select id="template-vcenter" value={vcenterId} disabled={vcenters.isLoading || vcenters.isError} onChange={(event) => { setVcenterId(event.target.value); setDatacenterId('') }}>
              <option value="">{vcenters.isLoading ? 'Loading vCenters…' : 'Select a vCenter'}</option>
              {(vcenters.data ?? []).filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </Select>
          </div>
          <div>
            <label className="field-label" htmlFor="template-datacenter">Target datacenter</label>
            <Select id="template-datacenter" value={datacenterId} disabled={!vcenterId || datacenters.isLoading || datacenters.isError} onChange={(event) => setDatacenterId(event.target.value)}>
              <option value="">{!vcenterId ? 'Select a vCenter first' : datacenters.isLoading ? 'Loading datacenters…' : 'Select a datacenter'}</option>
              {(datacenters.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </Select>
          </div>
          <div className="relative">
            <label className="field-label" htmlFor="template-search">Search packages</label>
            <Search className="pointer-events-none absolute bottom-3 left-3 h-4 w-4 text-[#87908a]" />
            <Input id="template-search" className="pl-9" placeholder="Name, type, storage, or description" value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
          <Button variant="ghost" size="sm" onClick={() => setSearch('')} disabled={!search}>Clear</Button>
        </div>
      </section>

      {vcenters.isLoading ? (
        <LoadingState title="Loading infrastructure connections" description="Retrieving vCenters that can provide deployment packages." />
      ) : vcenters.isError ? (
        <EmptyState title="vCenter connections could not be loaded" description={vcenters.error instanceof Error ? vcenters.error.message : 'Try again in a moment.'} action={<Button variant="secondary" onClick={() => void vcenters.refetch()}><RefreshCw className="h-4 w-4" /> Try again</Button>} />
      ) : vcenterId && datacenters.isLoading ? (
        <LoadingState title="Loading datacenters" description="Retrieving locations available through the selected vCenter." />
      ) : datacenters.isError ? (
        <EmptyState title="Datacenters could not be loaded" description={datacenters.error instanceof Error ? datacenters.error.message : 'Try the selected vCenter again.'} action={<Button variant="secondary" onClick={() => void datacenters.refetch()}><RefreshCw className="h-4 w-4" /> Try again</Button>} />
      ) : !vcenterId || !datacenterId ? (
        <EmptyState title="Choose a deployment target to browse packages" description="The target is carried into the deployment flow and all location-owned inventory is refreshed when it changes." />
      ) : templates.isLoading ? (
        <LoadingState title="Loading OVF and OVA packages" description="Retrieving deployment packages from the selected vCenter library." />
      ) : templates.isError ? (
        <EmptyState
          title="Package inventory could not be loaded"
          description={templates.error instanceof Error ? templates.error.message : 'Try again in a moment.'}
          action={<Button variant="secondary" onClick={() => void templates.refetch()}><RefreshCw className="h-4 w-4" /> Try again</Button>}
        />
      ) : (templates.data ?? []).length === 0 ? (
        <EmptyState title="No OVF or OVA packages are available for this target" description="Publish a package to the vCenter content library or choose another deployment target." />
      ) : items.length === 0 ? (
        <EmptyState title="No packages match your search" description="Clear the search to show all deployment packages." />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <article key={item.id} className="flex min-h-64 flex-col rounded-2xl border border-[#d8ddd7] bg-white p-5 shadow-[var(--ui-shadow)]">
              <div className="flex items-start gap-3">
                <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-700"><FileArchive className="h-5 w-5" /></span>
                <div className="min-w-0 flex-1">
                  <h2 className="line-clamp-2 text-base font-semibold tracking-[-0.02em] text-[#1c2520]">{item.name}</h2>
                  <div className="mt-2 flex flex-wrap gap-2"><Badge tone="info">{item.type}</Badge><Badge tone="neutral">{item.datacenter_name ?? 'vCenter content library'}</Badge></div>
                </div>
              </div>
              <p className="mt-4 line-clamp-3 text-xs leading-5 text-[#68736d]">{item.description || 'No package description is available.'}</p>
              <dl className="mt-5 space-y-2 border-t border-[#e4e8e3] pt-4 text-xs">
                <div className="flex items-center justify-between gap-3"><dt className="flex items-center gap-2 text-[#758079]"><HardDrive className="h-3.5 w-3.5" /> Storage</dt><dd className="max-w-[60%] truncate font-medium">{item.storage_name ?? 'Not available'}</dd></div>
                <div className="flex items-center justify-between gap-3"><dt className="text-[#758079]">Package size</dt><dd className="font-medium">{formatBytes(item.size_bytes)}</dd></div>
                <div className="flex items-center justify-between gap-3"><dt className="text-[#758079]">Updated</dt><dd className="font-medium">{formatDateTime(item.last_modified)}</dd></div>
                {item.location && <div className="flex items-start justify-between gap-3"><dt className="flex items-center gap-2 text-[#758079]"><Building2 className="h-3.5 w-3.5" /> Location</dt><dd className="max-w-[60%] truncate font-medium" title={item.location}>{item.location}</dd></div>}
              </dl>
              <Button className="mt-auto w-full" onClick={() => { window.location.href = `/provisioning/new?template_id=${encodeURIComponent(item.id)}&vcenter_id=${encodeURIComponent(vcenterId)}&datacenter_id=${encodeURIComponent(datacenterId)}` }}>
                Deploy package <ArrowRight className="h-4 w-4" />
              </Button>
            </article>
          ))}
        </div>
      )}

      <Link to="/provisioning/new" className="inline-flex items-center gap-2 text-xs font-semibold text-brand-700 hover:underline">Create a blank VM instead <ArrowRight className="h-3.5 w-3.5" /></Link>
    </div>
  )
}
