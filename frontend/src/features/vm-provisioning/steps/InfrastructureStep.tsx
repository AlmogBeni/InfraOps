import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { FormRow, Input, Select } from '@/components/ui/form-controls'
import { cn } from '@/lib/utils'
import { useWizard } from '@/features/vm-provisioning/context'
import {
  useClusters,
  useDatacenters,
  useHosts,
  useResourcePools,
  useSites,
  useTemplates,
  useVcenters,
} from '@/features/vm-provisioning/hooks'

export function InfrastructureStep() {
  const wizard = useWizard()
  const data = wizard.data
  const [templateSearch, setTemplateSearch] = useState('')

  const vcenters = useVcenters()
  const sites = useSites()
  const datacenters = useDatacenters(data.vcenter_id)
  const clusters = useClusters(data.vcenter_id, data.datacenter_id)
  const hosts = useHosts(data.vcenter_id, data.cluster_id)
  const pools = useResourcePools(data.vcenter_id, data.cluster_id)
  const templates = useTemplates(
    data.vcenter_id,
    data.datacenter_id,
    data.source_type === 'template',
  )

  const selectedCluster = clusters.data?.find((cluster) => cluster.id === data.cluster_id)
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
      site_id: '',
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
  }

  function changeCluster(clusterId: string) {
    wizard.update({
      cluster_id: clusterId,
      host_mode: 'auto',
      host_id: null,
      resource_pool_id: null,
      datastore_id: null,
    })
  }

  function changeHost(hostId: string) {
    wizard.update({
      host_mode: hostId ? 'manual' : 'auto',
      host_id: hostId || null,
    })
  }

  return (
    <section aria-label="Infrastructure selection" className="space-y-5">
      <header>
        <h2 className="text-sm font-semibold text-slate-900">Infrastructure and placement</h2>
        <p className="mt-1 text-xs text-slate-500">
          Select the target in dependency order. Downstream values are cleared whenever their parent changes.
        </p>
      </header>

      {(vcenters.isError || sites.isError) && (
        <Alert tone="danger" title="Infrastructure catalog could not be loaded">
          <span>Check connectivity and try again.</span>{' '}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => { void vcenters.refetch(); void sites.refetch() }}
          >
            Retry
          </Button>
        </Alert>
      )}

      <div className="grid grid-cols-1 gap-x-4 lg:grid-cols-2">
        <FormRow label="vCenter" htmlFor="vcenter" required error={wizard.errors.vcenter_id}>
          <Select
            id="vcenter"
            value={data.vcenter_id}
            onChange={(event) => changeVcenter(event.target.value)}
            disabled={vcenters.isLoading || vcenters.isError}
          >
            <option value="">{vcenters.isLoading ? 'Loading vCenters…' : 'Select a vCenter'}</option>
            {(vcenters.data ?? []).filter((entry) => entry.enabled).map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.name} ({entry.host})</option>
            ))}
          </Select>
        </FormRow>

        <FormRow label="Site" htmlFor="site" required error={wizard.errors.site_id}>
          <Select
            id="site"
            value={data.site_id}
            onChange={(event) => wizard.update({ site_id: event.target.value })}
            disabled={!data.vcenter_id || sites.isLoading || sites.isError}
          >
            <option value="">{!data.vcenter_id ? 'Select a vCenter first' : 'Select a site'}</option>
            {(sites.data ?? [])
              .filter((site) => site.vcenter_id === data.vcenter_id && site.enabled)
              .map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
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
                ? 'Select a vCenter first'
                : datacenters.isLoading
                  ? 'Loading datacenters…'
                  : datacenters.isError
                    ? 'Datacenter retrieval failed'
                    : 'Select a datacenter'}
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
                ? 'Select a datacenter first'
                : clusters.isLoading
                  ? 'Loading clusters…'
                  : clusters.isError
                    ? 'Cluster retrieval failed'
                    : (clusters.data ?? []).length === 0
                      ? 'No clusters are available'
                      : 'Select a cluster'}
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
          label="Host"
          htmlFor="host"
          required={data.host_mode === 'manual'}
          hint={
            !data.cluster_id
              ? 'Select a cluster first.'
              : selectedCluster?.drs_enabled
                ? 'Automatic placement lets DRS choose a healthy host.'
                : 'This cluster does not use DRS; select a host explicitly.'
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
                ? 'Select a cluster first'
                : hosts.isLoading
                  ? 'Loading hosts…'
                  : hosts.isError
                    ? 'Host retrieval failed'
                    : (hosts.data ?? []).length === 0
                      ? 'No hosts are available in this cluster'
                      : 'Automatic placement'}
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
              <Button type="button" size="sm" variant="ghost" onClick={() => void hosts.refetch()}>
                Retry hosts
              </Button>
            </div>
          )}
        </FormRow>

        <FormRow label="Resource pool" htmlFor="resource-pool" hint="Optional; defaults to the cluster root pool.">
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
        <div className="flex flex-wrap items-center gap-2 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
          <span>{selectedCluster.total_cpu_cores} cores</span>
          <span>·</span>
          <span>{selectedCluster.total_memory_gb.toFixed(0)} GB memory</span>
          <Badge tone={selectedCluster.drs_enabled ? 'success' : 'warning'}>
            DRS {selectedCluster.drs_enabled ? 'enabled' : 'disabled'}
          </Badge>
        </div>
      )}

      {data.source_type === 'template' && (
        <div className="space-y-3 border-t border-slate-200 pt-4">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">VM template</h3>
            <p className="text-xs text-slate-500">Templates are retrieved from the selected vCenter datacenter.</p>
          </div>

          {!data.datacenter_id ? (
            <Alert tone="info">Select a datacenter to retrieve its VM templates.</Alert>
          ) : templates.isLoading ? (
            <div className="flex h-24 items-center justify-center gap-2 text-xs text-slate-500">
              <Spinner /> Loading templates from vCenter…
            </div>
          ) : templates.isError ? (
            <Alert tone="danger" title="Template retrieval failed">
              <span>{templates.error instanceof Error ? templates.error.message : 'Templates could not be loaded.'}</span>{' '}
              <Button type="button" size="sm" variant="secondary" onClick={() => void templates.refetch()}>
                Retry
              </Button>
            </Alert>
          ) : (templates.data ?? []).length === 0 ? (
            <EmptyState
              title="No VM templates are available for the selected infrastructure."
              description="Verify that templates exist in this datacenter and that the vCenter service account can read them."
              action={<Button type="button" size="sm" variant="secondary" onClick={() => void templates.refetch()}>Retry</Button>}
            />
          ) : (
            <>
              <Input
                type="search"
                aria-label="Search templates"
                placeholder="Search templates by name or operating system…"
                value={templateSearch}
                onChange={(event) => setTemplateSearch(event.target.value)}
              />
              {filteredTemplates.length === 0 ? (
                <EmptyState title="No templates match this search." />
              ) : (
                <div className="grid grid-cols-1 gap-2" role="radiogroup" aria-label="VM template">
                  {filteredTemplates.map((template) => {
                    const selected = data.template_id === template.id
                    const metadata = [
                      template.os_version || template.os_family,
                      template.cpu ? `${template.cpu} CPU` : null,
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
                          'rounded-md border px-3 py-2 text-left',
                          selected ? 'border-brand-500 bg-brand-50/50 ring-1 ring-brand-500' : 'border-slate-200 hover:bg-slate-50',
                        )}
                      >
                        <span className="block text-sm font-medium text-slate-900">{template.name}</span>
                        {metadata && <span className="mt-0.5 block text-xs text-slate-500">{metadata}</span>}
                        {template.description && <span className="mt-1 block text-xs text-slate-500">{template.description}</span>}
                      </button>
                    )
                  })}
                </div>
              )}
              {wizard.errors.template_id && (
                <p className="text-xs font-medium text-red-600" role="alert">{wizard.errors.template_id}</p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}
