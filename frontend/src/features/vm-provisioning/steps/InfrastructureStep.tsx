import { Check, Database, ServerCog } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { FormRow, Input, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import {
  useClusters,
  useDatacenters,
  useHosts,
  useResourcePools,
  useTemplates,
  useVcenters,
} from '@/features/vm-provisioning/hooks'
import { cn } from '@/lib/utils'

export function InfrastructureStep() {
  const wizard = useWizard()
  const data = wizard.data
  const [templateSearch, setTemplateSearch] = useState('')

  const vcenters = useVcenters()
  const datacenters = useDatacenters(data.vcenter_id)
  const clusters = useClusters(data.vcenter_id, data.datacenter_id)
  const hosts = useHosts(data.vcenter_id, data.cluster_id)
  const pools = useResourcePools(data.vcenter_id, data.cluster_id)
  const templates = useTemplates(
    data.vcenter_id,
    data.datacenter_id,
    data.source_type === 'template',
  )
  const diagnoseAllTemplates = Boolean(
    data.source_type === 'template'
      && data.vcenter_id
      && data.datacenter_id
      && templates.isSuccess
      && templates.data.length === 0,
  )
  const allTemplates = useTemplates(data.vcenter_id, null, diagnoseAllTemplates)

  const selectedCluster = clusters.data?.find((cluster) => cluster.id === data.cluster_id)
  const selectedDatacenter = datacenters.data?.find((entry) => entry.id === data.datacenter_id)
  const filteredTemplates = useMemo(() => {
    const query = templateSearch.trim().toLowerCase()
    if (!query) return templates.data ?? []
    return (templates.data ?? []).filter((template) =>
      [template.name, template.os_family, template.os_version, template.description]
        .join(' ')
        .toLowerCase()
        .includes(query),
    )
  }, [templateSearch, templates.data])

  function changeVcenter(vcenterId: string) {
    wizard.update({
      vcenter_id: vcenterId,
      datacenter_id: '',
      cluster_id: '',
      host_mode: 'auto',
      host_id: null,
      resource_pool_id: null,
      datastore_id: null,
      network_id: '',
      template_id: '',
    })
  }

  function changeDatacenter(datacenterId: string) {
    wizard.update({
      datacenter_id: datacenterId,
      cluster_id: '',
      host_mode: 'auto',
      host_id: null,
      resource_pool_id: null,
      datastore_id: null,
      network_id: '',
      template_id: '',
    })
    setTemplateSearch('')
  }

  function changeCluster(clusterId: string) {
    const cluster = clusters.data?.find((entry) => entry.id === clusterId)
    wizard.update({
      cluster_id: clusterId,
      host_mode: cluster && !cluster.drs_enabled ? 'manual' : 'auto',
      host_id: null,
      resource_pool_id: null,
      datastore_id: null,
    })
  }

  function changeHost(hostId: string) {
    wizard.update({
      host_mode: hostId || selectedCluster?.drs_enabled ? (hostId ? 'manual' : 'auto') : 'manual',
      host_id: hostId || null,
    })
  }

  return (
    <section aria-label="Infrastructure selection" className="space-y-5">
      <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Placement</p>
            <h2>Target infrastructure</h2>
            <p>Select the hierarchy in order. Changing an upstream target clears all dependent values.</p>
          </div>
          <Badge tone="success"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Live inventory</Badge>
        </div>
      </header>

      {vcenters.isError && (
        <Alert tone="danger" title="vCenter catalog unavailable">
          <span>{vcenters.error instanceof Error ? vcenters.error.message : 'Connections could not be loaded.'}</span>{' '}
          <Button type="button" size="sm" variant="secondary" onClick={() => void vcenters.refetch()}>Retry</Button>
        </Alert>
      )}

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Placement hierarchy</p>
            <p className="console-group-description">vCenter → Datacenter → Cluster → Host</p>
          </div>
          <ServerCog className="h-4 w-4 text-slate-400" aria-hidden />
        </div>
        <div className="console-group-body grid grid-cols-1 gap-x-4 md:grid-cols-2">
          <FormRow label="vCenter" htmlFor="vcenter" required error={wizard.errors.vcenter_id}>
            <Select
              id="vcenter"
              value={data.vcenter_id}
              onChange={(event) => changeVcenter(event.target.value)}
              disabled={vcenters.isLoading || vcenters.isError}
            >
              <option value="">{vcenters.isLoading ? 'Loading vCenters…' : 'Select vCenter'}</option>
              {(vcenters.data ?? []).filter((entry) => entry.enabled).map((entry) => (
                <option key={entry.id} value={entry.id}>{entry.name} · {entry.host}</option>
              ))}
            </Select>
          </FormRow>

          <FormRow label="Datacenter" htmlFor="datacenter" required error={wizard.errors.datacenter_id}>
            <Select
              id="datacenter"
              value={data.datacenter_id}
              onChange={(event) => changeDatacenter(event.target.value)}
              disabled={!data.vcenter_id || datacenters.isLoading || datacenters.isError}
            >
              <option value="">
                {!data.vcenter_id
                  ? 'Select vCenter first'
                  : datacenters.isLoading
                    ? 'Loading datacenters…'
                    : datacenters.isError
                      ? 'Datacenter retrieval failed'
                      : 'Select datacenter'}
              </option>
              {(datacenters.data ?? []).map((dc) => <option key={dc.id} value={dc.id}>{dc.name}</option>)}
            </Select>
            {datacenters.isError && (
              <Button type="button" size="sm" variant="ghost" onClick={() => void datacenters.refetch()}>
                Retry datacenters
              </Button>
            )}
          </FormRow>

          <FormRow label="Cluster" htmlFor="cluster" required error={wizard.errors.cluster_id}>
            <Select
              id="cluster"
              value={data.cluster_id}
              onChange={(event) => changeCluster(event.target.value)}
              disabled={!data.datacenter_id || clusters.isLoading || clusters.isError}
            >
              <option value="">
                {!data.datacenter_id
                  ? 'Select datacenter first'
                  : clusters.isLoading
                    ? 'Loading clusters…'
                    : clusters.isError
                      ? 'Cluster retrieval failed'
                      : (clusters.data ?? []).length === 0
                        ? 'No clusters available'
                        : 'Select cluster'}
              </option>
              {(clusters.data ?? []).map((cluster) => (
                <option key={cluster.id} value={cluster.id}>
                  {cluster.name} · {cluster.hosts_count} host(s){cluster.drs_enabled ? ' · DRS' : ''}
                </option>
              ))}
            </Select>
            {clusters.isError && (
              <Button type="button" size="sm" variant="ghost" onClick={() => void clusters.refetch()}>
                Retry clusters
              </Button>
            )}
          </FormRow>

          <FormRow
            label="Host placement"
            htmlFor="host"
            required={data.host_mode === 'manual'}
            hint={
              !data.cluster_id
                ? 'Select a cluster first.'
                : selectedCluster?.drs_enabled
                  ? 'Leave automatic to let DRS place the VM, or pin a specific host.'
                  : 'DRS is disabled; select a host explicitly.'
            }
            error={wizard.errors.host_id}
          >
            <Select
              id="host"
              value={data.host_mode === 'manual' ? (data.host_id ?? '') : ''}
              onChange={(event) => changeHost(event.target.value)}
              disabled={!data.cluster_id || hosts.isLoading || hosts.isError || (hosts.data ?? []).length === 0}
            >
              <option value="">
                {!data.cluster_id
                  ? 'Select cluster first'
                  : hosts.isLoading
                    ? 'Loading hosts…'
                    : hosts.isError
                      ? 'Host retrieval failed'
                      : (hosts.data ?? []).length === 0
                        ? 'No hosts are available in this cluster'
                        : selectedCluster?.drs_enabled
                          ? 'Automatic placement (DRS)'
                          : 'Select host'}
              </option>
              {(hosts.data ?? []).map((host) => (
                <option key={host.id} value={host.id} disabled={!host.available_for_provisioning}>
                  {host.name}{host.available_for_provisioning ? '' : ` · ${host.maintenance_mode ? 'maintenance' : host.connection_state}`}
                </option>
              ))}
            </Select>
            {hosts.isError && (
              <div className="mt-1 flex items-center gap-2">
                <p className="text-xs text-red-600">
                  {hosts.error instanceof Error ? hosts.error.message : 'Hosts could not be retrieved.'}
                </p>
                <Button type="button" size="sm" variant="ghost" onClick={() => void hosts.refetch()}>Retry hosts</Button>
              </div>
            )}
          </FormRow>

          <FormRow
            label="Resource pool"
            htmlFor="resource-pool"
            hint="Optional. Defaults to the selected cluster's root resource pool."
            className="md:col-span-2"
          >
            <Select
              id="resource-pool"
              value={data.resource_pool_id ?? ''}
              onChange={(event) => wizard.update({ resource_pool_id: event.target.value || null })}
              disabled={!data.cluster_id || pools.isLoading || pools.isError}
            >
              <option value="">{pools.isLoading ? 'Loading resource pools…' : 'Cluster default'}</option>
              {(pools.data ?? []).map((pool) => <option key={pool.id} value={pool.id}>{pool.name}</option>)}
            </Select>
          </FormRow>
        </div>

        {selectedCluster && (
          <div className="grid grid-cols-2 divide-x divide-y divide-slate-200 border-t border-slate-200 bg-slate-50 sm:grid-cols-4 sm:divide-y-0">
            {[
              ['Hosts', String(selectedCluster.hosts_count)],
              ['CPU capacity', `${selectedCluster.total_cpu_cores} cores`],
              ['Memory capacity', `${selectedCluster.total_memory_gb.toFixed(0)} GB`],
              ['Placement', selectedCluster.drs_enabled ? 'DRS enabled' : 'Manual host'],
            ].map(([label, value]) => (
              <div key={label} className="px-3 py-2.5">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
                <p className="mt-0.5 text-xs font-semibold text-slate-800">{value}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {data.source_type === 'template' && (
        <div className="console-group">
          <div className="console-group-header">
            <div>
              <p className="console-group-title">Source template</p>
              <p className="console-group-description">Live templates scoped to the selected datacenter.</p>
            </div>
            <Database className="h-4 w-4 text-slate-400" aria-hidden />
          </div>

          {!data.datacenter_id ? (
            <div className="p-4"><Alert tone="info">Select a datacenter to retrieve templates.</Alert></div>
          ) : templates.isLoading ? (
            <div className="flex h-24 items-center justify-center gap-2 text-xs text-slate-500">
              <Spinner /> Loading templates from vCenter…
            </div>
          ) : templates.isError ? (
            <div className="p-4">
              <Alert tone="danger" title="Template retrieval failed">
                <span>{templates.error instanceof Error ? templates.error.message : 'Templates could not be loaded.'}</span>{' '}
                <Button type="button" size="sm" variant="secondary" onClick={() => void templates.refetch()}>Retry</Button>
              </Alert>
            </div>
          ) : (templates.data ?? []).length === 0 ? (
            <div className="divide-y divide-slate-200">
              {allTemplates.isLoading ? (
                <div className="flex h-24 items-center justify-center gap-2 text-xs text-slate-500">
                  <Spinner /> Checking template inventory across this vCenter…
                </div>
              ) : (
                <EmptyState
                  title={
                    allTemplates.isError
                      ? 'Datacenter returned no templates; the wider inventory check failed.'
                      : (allTemplates.data ?? []).length > 0
                      ? 'Templates are visible, but none belong to this datacenter.'
                      : 'vCenter returned no classic VM templates.'
                  }
                  description={
                    allTemplates.isError
                      ? (allTemplates.error instanceof Error ? allTemplates.error.message : 'The vCenter-wide diagnostic query could not be completed.')
                      : (allTemplates.data ?? []).length > 0
                      ? `${allTemplates.data?.length ?? 0} template(s) are visible in other datacenters. Select the matching datacenter or move the template in vCenter.`
                      : 'The inventory query completed successfully. Confirm the source is converted to a classic VM template. Content Library VM Templates are not clone-compatible with this workflow.'
                  }
                  action={(
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      onClick={() => void Promise.all([templates.refetch(), allTemplates.refetch()])}
                    >
                      Refresh inventory
                    </Button>
                  )}
                />
              )}
              <div className="grid gap-3 bg-slate-950 px-4 py-3 text-[11px] text-slate-300 sm:grid-cols-3">
                <div>
                  <p className="uppercase tracking-wider text-slate-500">Query scope</p>
                  <p className="mt-1 font-mono text-slate-100">{selectedDatacenter?.name ?? data.datacenter_id}</p>
                </div>
                <div>
                  <p className="uppercase tracking-wider text-slate-500">Datacenter MoRef</p>
                  <p className="mt-1 font-mono text-slate-100">{data.datacenter_id}</p>
                </div>
                <div>
                  <p className="uppercase tracking-wider text-slate-500">vCenter-wide templates</p>
                  <p className="mt-1 font-mono text-slate-100">
                    {allTemplates.isError ? 'Diagnostic failed' : allTemplates.data?.length ?? 'Checking'}
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-4 py-3">
                <Input
                  type="search"
                  aria-label="Search templates"
                  placeholder="Search name, OS, or description"
                  value={templateSearch}
                  onChange={(event) => setTemplateSearch(event.target.value)}
                  className="max-w-md"
                />
                <span className="text-xs text-slate-500">{filteredTemplates.length} template(s)</span>
              </div>
              {filteredTemplates.length === 0 ? (
                <EmptyState title="No templates match this search." />
              ) : (
                <div className="max-h-80 divide-y divide-slate-200 overflow-y-auto" role="radiogroup" aria-label="VM template">
                  {filteredTemplates.map((template) => {
                    const selected = data.template_id === template.id
                    const metadata = [
                      template.os_version || template.os_family,
                      template.cpu ? `${template.cpu} vCPU` : null,
                      template.memory_mb ? `${Math.round(template.memory_mb / 1024)} GB RAM` : null,
                      template.disk_size_gb ? `${template.disk_size_gb} GB disk` : null,
                    ].filter(Boolean).join(' · ')
                    return (
                      <button
                        key={template.id}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => wizard.update({ template_id: template.id })}
                        className={cn(
                          'grid w-full grid-cols-[24px_minmax(0,1fr)] gap-3 px-4 py-3 text-left',
                          selected ? 'bg-brand-50/70' : 'hover:bg-slate-50',
                        )}
                      >
                        <span className={cn(
                          'mt-0.5 flex h-5 w-5 items-center justify-center rounded-full border',
                          selected ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-300 bg-white',
                        )}>
                          {selected && <Check className="h-3 w-3" />}
                        </span>
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-slate-900">{template.name}</span>
                          {metadata && <span className="mt-0.5 block font-mono text-[11px] text-slate-600">{metadata}</span>}
                          {template.description && <span className="mt-1 block truncate text-xs text-slate-500">{template.description}</span>}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )}
              {wizard.errors.template_id && (
                <p className="border-t border-red-100 bg-red-50 px-4 py-2 text-xs font-medium text-red-700" role="alert">
                  {wizard.errors.template_id}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}
