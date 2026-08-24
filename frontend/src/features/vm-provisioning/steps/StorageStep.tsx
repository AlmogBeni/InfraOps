import { Badge } from '@/components/ui/feedback'
import { FormRow, RadioGroup, Select } from '@/components/ui/form-controls'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { useWizard } from '@/features/vm-provisioning/context'
import {
  useDatastoreClusters,
  useDatastores,
} from '@/features/vm-provisioning/hooks'

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

  return (
    <section aria-label="Storage selection" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-800">Datastore</h2>
        <p className="text-xs text-slate-500">
          Capacity is validated against the disks configured in the next step. Datastore clusters (Storage
          DRS) are listed separately.
        </p>
      </header>

      <FormRow label="Datastore selection" required>
        <RadioGroup
          name="storage-mode"
          value={data.storage_mode}
          onChange={(value) =>
            wizard.update({ storage_mode: value, datastore_id: value === 'auto' ? null : data.datastore_id })
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
          <FormRow label="Datastore" htmlFor="datastore" error={wizard.errors['datastore_id']}>
            <Select
              id="datastore"
              value={data.datastore_id ?? ''}
              onChange={(event) => wizard.update({ datastore_id: event.target.value || null })}
              disabled={!data.cluster_id}
            >
              <option value="">— Select a datastore —</option>
              {(datastores.data ?? [])
                .filter((entry) => entry.accessible)
                .map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name} · {entry.free_gb.toFixed(0)} GB free of {entry.capacity_gb.toFixed(0)} GB ({entry.type})
                  </option>
                ))}
            </Select>
          </FormRow>

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
              {(datastores.data ?? []).map((entry) => (
                <Tr key={entry.id} clickable={!entry.accessible ? false : true}
                    onClick={() => entry.accessible && wizard.update({ storage_mode: 'manual', datastore_id: entry.id })}>
                  <Td className="font-medium text-slate-800">
                    {entry.name} {!entry.accessible && <Badge tone="danger">Inaccessible</Badge>}
                  </Td>
                  <Td>{entry.type}</Td>
                  <Td>{entry.capacity_gb.toFixed(0)} GB</Td>
                  <Td>{entry.free_gb.toFixed(0)} GB</Td>
                  <Td>
                    <Badge tone={usageTone(entry.usage_percent)}>{entry.usage_percent.toFixed(0)}%</Badge>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </>
      )}

      {(datastoreClusters.data ?? []).length > 0 && (
        <div>
          <p className="field-label">Datastore clusters (Storage DRS)</p>
          <ul className="space-y-1 text-xs text-slate-600">
            {(datastoreClusters.data ?? []).map((cluster) => (
              <li key={cluster.id} className="rounded border border-slate-200 px-3 py-1.5">
                <span className="font-medium">{cluster.name}</span> · {cluster.free_gb.toFixed(0)} GB free of{' '}
                {cluster.capacity_gb.toFixed(0)} GB — select “Automatically select datastore” to let Storage
                DRS place the VM.
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
