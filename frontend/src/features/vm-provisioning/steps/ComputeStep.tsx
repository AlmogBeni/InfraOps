import { useEffect } from 'react'

import { Badge } from '@/components/ui/feedback'
import { FormRow, RadioGroup, Select } from '@/components/ui/form-controls'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { useWizard } from '@/features/vm-provisioning/context'
import { useClusters, useHosts, useResourcePools } from '@/features/vm-provisioning/hooks'

export function ComputeStep() {
  const wizard = useWizard()
  const data = wizard.data

  const clusters = useClusters(data.vcenter_id, data.datacenter_id)
  const hosts = useHosts(data.vcenter_id, data.cluster_id)
  const pools = useResourcePools(data.vcenter_id, data.cluster_id)

  const selectedCluster = clusters.data?.find((cluster) => cluster.id === data.cluster_id)

  // Reset placement when the cluster changes.
  useEffect(() => {
    if (!data.cluster_id) return
    wizard.update({ host_id: null, resource_pool_id: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.cluster_id])

  return (
    <section aria-label="Compute selection" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-800">Cluster & placement</h2>
        <p className="text-xs text-slate-500">
          DRS-enabled clusters can place the VM automatically; otherwise pick a host manually.
        </p>
      </header>

      <FormRow label="Cluster" htmlFor="cluster" required error={wizard.errors['cluster_id']}>
        <Select
          id="cluster"
          value={data.cluster_id}
          onChange={(event) => wizard.update({ cluster_id: event.target.value })}
          disabled={!data.datacenter_id}
        >
          <option value="">— Select a cluster —</option>
          {(clusters.data ?? []).map((cluster) => (
            <option key={cluster.id} value={cluster.id}>
              {cluster.name} · {cluster.hosts_count} host(s){cluster.drs_enabled ? ' · DRS' : ''}
            </option>
          ))}
        </Select>
      </FormRow>

      {selectedCluster && (
        <p className="text-xs text-slate-500">
          {selectedCluster.total_cpu_cores} cores · {selectedCluster.total_memory_gb.toFixed(0)} GB memory ·{' '}
          {selectedCluster.drs_enabled ? (
            <Badge tone="success">DRS enabled — automatic placement recommended</Badge>
          ) : (
            <Badge tone="warning">DRS disabled — manual host selection required</Badge>
          )}
        </p>
      )}

      <FormRow label="Host placement" required>
        <RadioGroup
          name="host-mode"
          value={data.host_mode}
          onChange={(value) => wizard.update({ host_mode: value, host_id: value === 'auto' ? null : data.host_id })}
          options={[
            { value: 'auto', label: 'Automatically select host', description: 'vCenter/DRS chooses the optimal host.' },
            {
              value: 'manual',
              label: 'Manually select host',
              description: 'Pin the VM to a specific ESXi host.',
              disabled: selectedCluster != null && !selectedCluster.drs_enabled ? false : false,
            },
          ]}
        />
      </FormRow>

      {data.host_mode === 'manual' && (
        <div>
          <p className="field-label">Available hosts</p>
          {hosts.isLoading ? (
            <p className="text-xs text-slate-400">Loading hosts…</p>
          ) : (hosts.data ?? []).length === 0 ? (
            <p className="text-xs text-slate-400">No hosts reported for this cluster.</p>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th />
                  <Th>Hostname</Th>
                  <Th>State</Th>
                  <Th>CPU</Th>
                  <Th>Memory</Th>
                </tr>
              </thead>
              <tbody>
                {(hosts.data ?? []).map((host) => (
                  <Tr key={host.id}>
                    <Td>
                      <input
                        type="radio"
                        name="host-select"
                        aria-label={`Select ${host.name}`}
                        disabled={!host.available_for_provisioning}
                        checked={data.host_id === host.id}
                        onChange={() => wizard.update({ host_id: host.id })}
                        className="h-4 w-4 border-slate-300 text-brand-600 focus:ring-brand-500"
                      />
                    </Td>
                    <Td className="font-medium text-slate-800">{host.name}</Td>
                    <Td>
                      {host.maintenance_mode ? (
                        <Badge tone="warning">Maintenance</Badge>
                      ) : host.connection_state === 'connected' ? (
                        <Badge tone="success">Connected</Badge>
                      ) : (
                        <Badge tone="danger">{host.connection_state}</Badge>
                      )}
                    </Td>
                    <Td>{host.cpu_usage_percent.toFixed(0)}%</Td>
                    <Td>{host.memory_usage_percent.toFixed(0)}%</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
          {wizard.errors['host_id'] && (
            <p className="mt-1 text-xs font-medium text-red-600">{wizard.errors['host_id']}</p>
          )}
        </div>
      )}

      <FormRow label="Resource pool" htmlFor="resource-pool" hint="Optional — defaults to the cluster root pool.">
        <Select
          id="resource-pool"
          value={data.resource_pool_id ?? ''}
          onChange={(event) => wizard.update({ resource_pool_id: event.target.value || null })}
          disabled={!data.cluster_id}
        >
          <option value="">Cluster default</option>
          {(pools.data ?? []).map((pool) => (
            <option key={pool.id} value={pool.id}>
              {pool.name}
            </option>
          ))}
        </Select>
      </FormRow>
    </section>
  )
}
