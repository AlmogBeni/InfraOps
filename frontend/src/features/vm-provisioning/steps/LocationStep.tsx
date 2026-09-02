import { Building2, Cpu, MemoryStick, RefreshCw, Server, Waypoints } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { FormRow, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import {
  useClusters,
  useDatacenters,
  useHosts,
  useResourcePools,
  useVcenters,
} from '@/features/vm-provisioning/hooks'
import { api } from '@/lib/api'

export function LocationStep() {
  const wizard = useWizard()
  const queryClient = useQueryClient()
  const updateWizard = wizard.update
  const data = wizard.data
  const vcenters = useVcenters()
  const datacenters = useDatacenters(data.vcenter_id)
  const clusters = useClusters(data.vcenter_id, data.datacenter_id)
  const hosts = useHosts(data.vcenter_id, data.cluster_id)
  const pools = useResourcePools(data.vcenter_id, data.cluster_id)
  const selectedCluster = clusters.data?.find((cluster) => cluster.id === data.cluster_id)
  const availableHosts = (hosts.data ?? []).filter((host) => host.available_for_provisioning)
  const noAvailableHosts = Boolean(
    data.cluster_id && !hosts.isLoading && !hosts.isError && availableHosts.length === 0,
  )

  useEffect(() => {
    if (
      data.vcenter_id
      && vcenters.isSuccess
      && !(vcenters.data ?? []).some((item) => item.enabled && item.id === data.vcenter_id)
    ) {
      updateWizard({ vcenter_id: '' })
      return
    }

    if (
      data.datacenter_id
      && datacenters.isSuccess
      && !(datacenters.data ?? []).some((item) => item.id === data.datacenter_id)
    ) {
      updateWizard({ datacenter_id: '' })
      return
    }

    if (
      data.cluster_id
      && clusters.isSuccess
      && !(clusters.data ?? []).some((item) => item.id === data.cluster_id)
    ) {
      updateWizard({ cluster_id: '' })
      return
    }

    if (
      data.host_id
      && hosts.isSuccess
      && !(hosts.data ?? []).some((item) => item.id === data.host_id && item.available_for_provisioning)
    ) {
      updateWizard({ host_id: null })
      return
    }

    if (
      data.resource_pool_id
      && pools.isSuccess
      && !(pools.data ?? []).some((item) => item.id === data.resource_pool_id)
    ) {
      updateWizard({ resource_pool_id: null })
    }
  }, [
    clusters.data,
    clusters.isSuccess,
    data.cluster_id,
    data.datacenter_id,
    data.host_id,
    data.resource_pool_id,
    data.vcenter_id,
    datacenters.data,
    datacenters.isSuccess,
    hosts.data,
    hosts.isSuccess,
    pools.data,
    pools.isSuccess,
    updateWizard,
    vcenters.data,
    vcenters.isSuccess,
  ])

  useEffect(() => {
    if (noAvailableHosts && data.host_mode === 'auto') {
      updateWizard({ host_mode: 'manual', host_id: null })
    }
  }, [data.host_mode, noAvailableHosts, updateWizard])

  useEffect(() => {
    if (!data.vcenter_id || !data.datacenter_id) return

    if (data.source_type === 'template') {
      void queryClient.prefetchQuery({
        queryKey: ['templates', data.vcenter_id, data.datacenter_id],
        queryFn: () => api.templates(data.vcenter_id, data.datacenter_id),
        staleTime: 5 * 60 * 1000,
      })
      return
    }

    void queryClient.prefetchQuery({
      queryKey: ['isos', data.vcenter_id, data.datacenter_id],
      queryFn: () => api.isos(data.vcenter_id, data.datacenter_id),
      staleTime: 5 * 60 * 1000,
    })
  }, [data.datacenter_id, data.source_type, data.vcenter_id, queryClient])

  if (vcenters.isLoading) {
    return <LoadingState title="Loading infrastructure connections" description="Retrieving the vCenters available to your account." />
  }

  if (vcenters.isError) {
    return (
      <EmptyState
        title="vCenter connections could not be loaded"
        description={vcenters.error instanceof Error ? vcenters.error.message : 'Try again in a moment.'}
        action={<Button type="button" variant="secondary" onClick={() => void vcenters.refetch()}><RefreshCw className="h-4 w-4" /> Try again</Button>}
      />
    )
  }

  if ((vcenters.data ?? []).filter((item) => item.enabled).length === 0) {
    return <EmptyState title="No vCenter connections are available" description="An administrator must configure and enable a vCenter connection before a VM can be created." />
  }

  return (
    <section aria-label="Deployment location" className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="console-kicker">Step 2 · Location</p>
          <h2>Where should this VM run?</h2>
          <p>Choose the datacenter first. Networks, ISOs, compute, and storage are scoped to this location; deployment packages follow the selected vCenter library.</p>
        </div>
        <Badge tone="info"><Waypoints className="h-3 w-3" /> Datacenter scoped</Badge>
      </header>

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Infrastructure hierarchy</p>
            <p className="console-group-description">Choose in order: connection, datacenter, compute target, then optional placement controls.</p>
          </div>
          <Building2 className="h-5 w-5 text-brand-700" aria-hidden />
        </div>

        <div className="console-group-body grid grid-cols-1 gap-x-5 md:grid-cols-2">
          <FormRow label="vCenter connection" htmlFor="vcenter" required error={wizard.errors.vcenter_id}>
            <Select
              id="vcenter"
              value={data.vcenter_id}
              onChange={(event) => wizard.update({ vcenter_id: event.target.value })}
            >
              <option value="">Select a vCenter</option>
              {(vcenters.data ?? []).filter((item) => item.enabled).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </Select>
          </FormRow>

          <FormRow label="Datacenter" htmlFor="datacenter" required error={wizard.errors.datacenter_id}>
            <Select
              id="datacenter"
              value={data.datacenter_id}
              disabled={!data.vcenter_id || datacenters.isLoading || datacenters.isError}
              onChange={(event) => wizard.update({ datacenter_id: event.target.value })}
            >
              <option value="">
                {!data.vcenter_id
                  ? 'Select a vCenter first'
                  : datacenters.isLoading
                    ? 'Loading datacenters…'
                    : datacenters.isError
                      ? 'Datacenters could not be loaded'
                      : (datacenters.data ?? []).length === 0
                        ? 'No datacenters available'
                        : 'Select a datacenter'}
              </option>
              {(datacenters.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </Select>
            {datacenters.isError && <Button type="button" size="sm" variant="ghost" onClick={() => void datacenters.refetch()}>Retry datacenters</Button>}
          </FormRow>

          <FormRow label="Compute target" htmlFor="cluster" required error={wizard.errors.cluster_id}>
            <Select
              id="cluster"
              value={data.cluster_id}
              disabled={!data.datacenter_id || clusters.isLoading || clusters.isError}
              onChange={(event) => {
                const selected = clusters.data?.find((item) => item.id === event.target.value)
                wizard.update({
                  cluster_id: event.target.value,
                  host_mode: selected && !selected.drs_enabled ? 'manual' : 'auto',
                })
              }}
            >
              <option value="">
                {!data.datacenter_id
                  ? 'Select a datacenter first'
                  : clusters.isLoading
                    ? 'Loading compute targets…'
                    : clusters.isError
                      ? 'Compute targets could not be loaded'
                      : (clusters.data ?? []).length === 0
                        ? 'No compute targets available'
                        : 'Select a compute target'}
              </option>
              {(clusters.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </Select>
            {clusters.isError && <Button type="button" size="sm" variant="ghost" onClick={() => void clusters.refetch()}>Retry compute targets</Button>}
          </FormRow>

          <FormRow
            label="Host placement"
            htmlFor="host"
            required={data.host_mode === 'manual'}
            error={wizard.errors.host_id}
            hint={selectedCluster?.drs_enabled ? 'Leave automatic to let DRS select a host.' : data.cluster_id ? 'This compute target requires an explicit host.' : undefined}
          >
            <Select
              id="host"
              value={data.host_mode === 'manual' ? (data.host_id ?? '') : ''}
              disabled={!data.cluster_id || hosts.isLoading || hosts.isError || availableHosts.length === 0}
              onChange={(event) => wizard.update({ host_mode: event.target.value ? 'manual' : 'auto', host_id: event.target.value || null })}
            >
              <option value="">
                {!data.cluster_id
                  ? 'Select a compute target first'
                  : hosts.isLoading
                    ? 'Loading hosts…'
                    : hosts.isError
                      ? 'Hosts could not be loaded'
                      : noAvailableHosts
                        ? 'No hosts available for provisioning'
                      : selectedCluster?.drs_enabled
                        ? 'Automatic placement (DRS)'
                        : 'Select a host'}
              </option>
              {(hosts.data ?? []).map((item) => (
                <option key={item.id} value={item.id} disabled={!item.available_for_provisioning}>
                  {item.name}{item.available_for_provisioning ? '' : ' · Unavailable'}
                </option>
              ))}
            </Select>
          </FormRow>

          <FormRow label="Resource pool" htmlFor="resource-pool" hint="Optional. The compute target's root pool is used by default." className="md:col-span-2">
            <Select
              id="resource-pool"
              value={data.resource_pool_id ?? ''}
              disabled={!data.cluster_id || pools.isLoading || pools.isError}
              onChange={(event) => wizard.update({ resource_pool_id: event.target.value || null })}
            >
              <option value="">Compute target default</option>
              {(pools.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </Select>
          </FormRow>
        </div>
      </div>

      {data.datacenter_id && !clusters.isLoading && !clusters.isError && (clusters.data ?? []).length === 0 && (
        <Alert tone="warning" title="No compute targets in this datacenter">No clusters or standalone ESXi hosts are visible to the configured vCenter account.</Alert>
      )}

      {data.cluster_id && hosts.isError && (
        <Alert tone="danger" title="Hosts could not be loaded">
          The selected compute target's host inventory is unavailable. <Button type="button" size="sm" variant="secondary" onClick={() => void hosts.refetch()}>Try again</Button>
        </Alert>
      )}

      {noAvailableHosts && (
        <Alert tone="warning" title="No hosts are available for provisioning">
          The selected compute target has no eligible hosts. Choose another compute target or{' '}
          <Button type="button" size="sm" variant="secondary" onClick={() => void hosts.refetch()}>check again</Button>
        </Alert>
      )}

      {data.cluster_id && pools.isError && (
        <Alert tone="warning" title="Resource pools could not be loaded">
          Default resource-pool placement remains available. <Button type="button" size="sm" variant="secondary" onClick={() => void pools.refetch()}>Try again</Button>
        </Alert>
      )}

      {selectedCluster && (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-2xl border border-[#d8ddd7] bg-white p-4">
            <Server className="h-4 w-4 text-brand-700" />
            <p className="mt-3 text-2xl font-semibold tracking-tight">{selectedCluster.hosts_count}</p>
            <p className="mt-1 text-xs text-[#68736d]">Hosts in {selectedCluster.name}</p>
          </div>
          <div className="rounded-2xl border border-[#d8ddd7] bg-white p-4">
            <Cpu className="h-4 w-4 text-brand-700" />
            <p className="mt-3 text-2xl font-semibold tracking-tight">{selectedCluster.total_cpu_cores}</p>
            <p className="mt-1 text-xs text-[#68736d]">CPU cores in the compute target</p>
          </div>
          <div className="rounded-2xl border border-[#d8ddd7] bg-white p-4">
            <MemoryStick className="h-4 w-4 text-brand-700" />
            <p className="mt-3 text-2xl font-semibold tracking-tight">{selectedCluster.total_memory_gb.toFixed(0)} GB</p>
            <p className="mt-1 text-xs text-[#68736d]">Total compute-target memory</p>
          </div>
        </div>
      )}
    </section>
  )
}
