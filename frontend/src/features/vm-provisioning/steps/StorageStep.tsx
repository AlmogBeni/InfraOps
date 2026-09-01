import { Database, Layers3 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { FormRow, RadioGroup, Select } from '@/components/ui/form-controls'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { useWizard } from '@/features/vm-provisioning/context'
import { useDatastoreClusters, useDatastores } from '@/features/vm-provisioning/hooks'

function usageTone(percent: number): 'success' | 'warning' | 'danger' {
  if (percent >= 90) return 'danger'
  if (percent >= 75) return 'warning'
  return 'success'
}

export function StorageStep() {
  const wizard = useWizard()
  const data = wizard.data
  const datastores = useDatastores(data.vcenter_id, data.cluster_id)
  const datastoreClusters = useDatastoreClusters(data.vcenter_id, data.cluster_id)
  const items = datastores.data ?? []

  return (
    <section aria-label="Storage selection" className="space-y-5">
      <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Storage placement</p>
            <h2>Datastore selection</h2>
            <p>Choose automatic capacity-aware placement or pin all requested disks to a datastore.</p>
          </div>
          <Badge tone="neutral">{items.length} datastore(s)</Badge>
        </div>
      </header>

      {datastores.isError && (
        <Alert tone="danger" title="Datastore retrieval failed">
          <span>{datastores.error instanceof Error ? datastores.error.message : 'Datastores could not be loaded.'}</span>{' '}
          <Button type="button" size="sm" variant="secondary" onClick={() => void datastores.refetch()}>Retry</Button>
        </Alert>
      )}

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Placement policy</p>
            <p className="console-group-description">Capacity is validated against the disks configured in Compute.</p>
          </div>
          <Database className="h-4 w-4 text-slate-400" aria-hidden />
        </div>
        <div className="console-group-body">
          <FormRow label="Datastore placement" required className="mb-0">
            <RadioGroup
              name="storage-mode"
              value={data.storage_mode}
              columns={2}
              onChange={(value) => wizard.update({ storage_mode: value, datastore_id: value === 'auto' ? null : data.datastore_id })}
              options={[
                {
                  value: 'auto',
                  label: 'Automatic placement',
                  description: 'Use the accessible datastore with the most available capacity.',
                },
                {
                  value: 'manual',
                  label: 'Specific datastore',
                  description: 'Pin the requested disks to an explicitly selected datastore.',
                },
              ]}
            />
          </FormRow>
        </div>
      </div>

      {data.storage_mode === 'manual' && (
        <div className="console-group">
          <div className="console-group-header">
            <div>
              <p className="console-group-title">Datastore inventory</p>
              <p className="console-group-description">Only accessible targets can be selected.</p>
            </div>
          </div>

          {datastores.isLoading ? (
            <div className="flex h-24 items-center justify-center gap-2 text-xs text-slate-500">
              <Spinner /> Loading datastores…
            </div>
          ) : !datastores.isError && data.cluster_id && items.length === 0 ? (
            <EmptyState
              title="No datastores are available in the selected cluster."
              action={<Button type="button" size="sm" variant="secondary" onClick={() => void datastores.refetch()}>Retry</Button>}
            />
          ) : (
            <>
              <div className="px-4 pt-4">
                <FormRow label="Selected datastore" htmlFor="datastore" required error={wizard.errors.datastore_id}>
                  <Select
                    id="datastore"
                    value={data.datastore_id ?? ''}
                    onChange={(event) => wizard.update({ datastore_id: event.target.value || null })}
                    disabled={!data.cluster_id || datastores.isError || items.length === 0}
                  >
                    <option value="">{!data.cluster_id ? 'Select a cluster first' : 'Select datastore'}</option>
                    {items.filter((entry) => entry.accessible).map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.name} · {entry.free_gb.toFixed(0)} GB free · {entry.type}
                      </option>
                    ))}
                  </Select>
                </FormRow>
              </div>

              {items.length > 0 && (
                <Table>
                  <thead>
                    <tr><Th>Datastore</Th><Th>Type</Th><Th>Capacity</Th><Th>Free</Th><Th>Usage</Th></tr>
                  </thead>
                  <tbody>
                    {items.map((entry) => (
                      <Tr
                        key={entry.id}
                        clickable={entry.accessible}
                        onClick={() => entry.accessible && wizard.update({ storage_mode: 'manual', datastore_id: entry.id })}
                        className={data.datastore_id === entry.id ? 'bg-brand-50/70' : undefined}
                      >
                        <Td className="font-medium text-slate-800">
                          {entry.name} {!entry.accessible && <Badge tone="danger">Inaccessible</Badge>}
                        </Td>
                        <Td>{entry.type}</Td>
                        <Td>{entry.capacity_gb.toFixed(0)} GB</Td>
                        <Td>{entry.free_gb.toFixed(0)} GB</Td>
                        <Td><Badge tone={usageTone(entry.usage_percent)}>{entry.usage_percent.toFixed(0)}%</Badge></Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </>
          )}
        </div>
      )}

      {datastoreClusters.isError && (
        <Alert tone="warning" title="Storage DRS inventory unavailable">
          Datastore clusters could not be displayed. Backend capacity validation remains active.
        </Alert>
      )}

      {(datastoreClusters.data ?? []).length > 0 && (
        <div className="console-group">
          <div className="console-group-header">
            <div>
              <p className="console-group-title">Storage DRS inventory</p>
              <p className="console-group-description">Visible when automatic placement is used.</p>
            </div>
            <Layers3 className="h-4 w-4 text-slate-400" aria-hidden />
          </div>
          <div className="divide-y divide-slate-200">
            {(datastoreClusters.data ?? []).map((cluster) => (
              <div key={cluster.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-xs">
                <span className="font-semibold text-slate-800">{cluster.name}</span>
                <span className="font-mono text-slate-500">
                  {cluster.free_gb.toFixed(0)} GB free / {cluster.capacity_gb.toFixed(0)} GB
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
