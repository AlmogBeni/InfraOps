import { Database, Layers3 } from 'lucide-react'
import { useEffect, useMemo } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { FormRow, RadioGroup, Select } from '@/components/ui/form-controls'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { useWizard } from '@/features/vm-provisioning/context'
import { useDatastoreClusters, useDatastores } from '@/features/vm-provisioning/hooks'

function usageTone(percent: number): 'success' | 'warning' | 'danger' {
  if (percent >= 90) return 'danger'
  if (percent >= 75) return 'warning'
  return 'success'
}

export function StorageStep({ embedded = false }: { embedded?: boolean }) {
  const wizard = useWizard()
  const updateWizard = wizard.update
  const data = wizard.data
  const datastores = useDatastores(data.vcenter_id, data.cluster_id)
  const datastoreClusters = useDatastoreClusters(data.vcenter_id, data.cluster_id)
  const items = datastores.data ?? []
  const accessibleItems = useMemo(
    () => (datastores.data ?? []).filter((entry) => entry.accessible),
    [datastores.data],
  )
  const noAccessibleDatastores = Boolean(
    data.cluster_id && datastores.isSuccess && accessibleItems.length === 0,
  )

  useEffect(() => {
    if (!datastores.isSuccess) return

    const accessibleIds = new Set(accessibleItems.map((entry) => entry.id))
    const staleDatastore = Boolean(data.datastore_id && !accessibleIds.has(data.datastore_id))
    const blockAutomaticPlacement = accessibleIds.size === 0 && data.storage_mode === 'auto'
    let disksChanged = false
    const disks = data.disks.map((disk) => {
      if (!disk.datastore_id || accessibleIds.has(disk.datastore_id)) return disk
      disksChanged = true
      return { ...disk, datastore_id: null }
    })

    if (staleDatastore || disksChanged || blockAutomaticPlacement) {
      updateWizard({
        ...(staleDatastore ? { datastore_id: null } : {}),
        ...(disksChanged ? { disks } : {}),
        ...(blockAutomaticPlacement ? { storage_mode: 'manual' } : {}),
      })
    }
  }, [accessibleItems, data.datastore_id, data.disks, data.storage_mode, datastores.isSuccess, updateWizard])

  return (
    <section aria-label="Storage selection" className="space-y-5">
      {!embedded && <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Storage placement</p>
            <h2>Datastore selection</h2>
            <p>Choose automatic capacity-aware placement or pin all requested disks to a datastore.</p>
          </div>
          <Badge tone="neutral">{accessibleItems.length} accessible datastore(s)</Badge>
        </div>
      </header>}

      {datastores.isError && (
        <Alert tone="danger" title="Datastore retrieval failed">
          <span>{datastores.error instanceof Error ? datastores.error.message : 'Datastores could not be loaded.'}</span>{' '}
          <Button type="button" size="sm" variant="secondary" onClick={() => void datastores.refetch()}>Retry</Button>
        </Alert>
      )}

      {datastores.isLoading && data.storage_mode === 'auto' && (
        <LoadingState title="Loading cluster storage" description="Checking accessible datastores and capacity for automatic placement." />
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
                  disabled: noAccessibleDatastores,
                },
                {
                  value: 'manual',
                  label: 'Specific datastore',
                  description: 'Pin the requested disks to an explicitly selected datastore.',
                  disabled: noAccessibleDatastores,
                },
              ]}
            />
          </FormRow>
        </div>
      </div>

      {(data.storage_mode === 'manual' || noAccessibleDatastores) && (
        <div className="console-group">
          <div className="console-group-header">
            <div>
              <p className="console-group-title">Datastore inventory</p>
              <p className="console-group-description">Only accessible targets can be selected.</p>
            </div>
          </div>

          {datastores.isLoading ? (
            <LoadingState title="Loading datastores" description="Checking accessible storage targets and current free capacity." />
          ) : !datastores.isError && data.cluster_id && accessibleItems.length === 0 ? (
            <EmptyState
              title="No accessible datastores are available in the selected cluster."
              description={items.length > 0 ? 'Storage was discovered, but every datastore is currently inaccessible.' : undefined}
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
                    disabled={!data.cluster_id || datastores.isError || accessibleItems.length === 0}
                  >
                    <option value="">{!data.cluster_id ? 'Select a cluster first' : 'Select datastore'}</option>
                    {accessibleItems.map((entry) => (
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
