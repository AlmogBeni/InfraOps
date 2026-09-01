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
  const datastoreItems = datastores.data ?? []

  return (
    <section aria-label="Storage selection" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-800">Datastore</h2>
        <p className="text-xs text-slate-500">
          Capacity is validated against the disks configured in Compute. Datastore clusters (Storage DRS)
          are listed separately.
        </p>
      </header>

      {datastores.isError && (
        <Alert tone="danger" title="Datastore retrieval failed">
          <span>
            {datastores.error instanceof Error
              ? datastores.error.message
              : 'Datastores could not be loaded.'}
          </span>{' '}
          <Button type="button" size="sm" variant="secondary" onClick={() => void datastores.refetch()}>
            Retry
          </Button>
        </Alert>
      )}

      {datastores.isLoading && data.cluster_id && (
        <div className="flex h-20 items-center justify-center gap-2 text-xs text-slate-500">
          <Spinner /> Loading datastores…
        </div>
      )}

      {!datastores.isLoading && !datastores.isError && data.cluster_id && datastoreItems.length === 0 && (
        <EmptyState
          title="No datastores are available in the selected cluster."
          action={
            <Button type="button" size="sm" variant="secondary" onClick={() => void datastores.refetch()}>
              Retry
            </Button>
          }
        />
      )}

      <FormRow label="Datastore selection" required>
        <RadioGroup
          name="storage-mode"
          value={data.storage_mode}
          onChange={(value) =>
            wizard.update({
              storage_mode: value,
              datastore_id: value === 'auto' ? null : data.datastore_id,
            })
          }
          options={[
            {
              value: 'auto',
              label: 'Automatically select datastore',
              description: 'The platform picks the accessible datastore with the most free space.',
            },
            { value: 'manual', label: 'Select datastore manually' },
          ]}
        />
      </FormRow>

      {data.storage_mode === 'manual' && (
        <>
          <FormRow label="Datastore" htmlFor="datastore" required error={wizard.errors.datastore_id}>
            <Select
              id="datastore"
              value={data.datastore_id ?? ''}
              onChange={(event) => wizard.update({ datastore_id: event.target.value || null })}
              disabled={
                !data.cluster_id ||
                datastores.isLoading ||
                datastores.isError ||
                datastoreItems.length === 0
              }
            >
              <option value="">
                {!data.cluster_id
                  ? 'Select a cluster first'
                  : datastores.isLoading
                    ? 'Loading datastores…'
                    : datastores.isError
                      ? 'Datastore retrieval failed'
                      : datastoreItems.length === 0
                        ? 'No datastores are available'
                        : 'Select a datastore'}
              </option>
              {datastoreItems
                .filter((entry) => entry.accessible)
                .map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name} · {entry.free_gb.toFixed(0)} GB free of{' '}
                    {entry.capacity_gb.toFixed(0)} GB ({entry.type})
                  </option>
                ))}
            </Select>
          </FormRow>

          {!datastores.isLoading && !datastores.isError && datastoreItems.length > 0 && (
            <Table>
              <thead>
                <tr>
                  <Th>Datastore</Th>
                  <Th>Type</Th>
                  <Th>Capacity</Th>
                  <Th>Free</Th>
                  <Th>Usage</Th>
                </tr>
              </thead>
              <tbody>
                {datastoreItems.map((entry) => (
                  <Tr
                    key={entry.id}
                    clickable={entry.accessible}
                    onClick={() =>
                      entry.accessible &&
                      wizard.update({ storage_mode: 'manual', datastore_id: entry.id })
                    }
                  >
                    <Td className="font-medium text-slate-800">
                      {entry.name} {!entry.accessible && <Badge tone="danger">Inaccessible</Badge>}
                    </Td>
                    <Td>{entry.type}</Td>
                    <Td>{entry.capacity_gb.toFixed(0)} GB</Td>
                    <Td>{entry.free_gb.toFixed(0)} GB</Td>
                    <Td>
                      <Badge tone={usageTone(entry.usage_percent)}>
                        {entry.usage_percent.toFixed(0)}%
                      </Badge>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </>
      )}

      {datastoreClusters.isError && (
        <Alert tone="warning" title="Datastore cluster inventory unavailable">
          Storage DRS targets could not be displayed. Automatic placement will still be validated by the
          backend.
        </Alert>
      )}

      {(datastoreClusters.data ?? []).length > 0 && (
        <div>
          <p className="field-label">Datastore clusters (Storage DRS)</p>
          <ul className="space-y-1 text-xs text-slate-600">
            {(datastoreClusters.data ?? []).map((cluster) => (
              <li key={cluster.id} className="rounded border border-slate-200 px-3 py-1.5">
                <span className="font-medium">{cluster.name}</span> · {cluster.free_gb.toFixed(0)} GB free
                of {cluster.capacity_gb.toFixed(0)} GB — select “Automatically select datastore” to let
                Storage DRS place the VM.
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
