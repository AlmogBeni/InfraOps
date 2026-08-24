import { Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox, FormRow, Input, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'

const CPU_CHOICES = [1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64]
const MEMORY_GB_CHOICES = [2, 4, 8, 16, 24, 32, 48, 64, 96, 128, 256]

export function HardwareStep() {
  const wizard = useWizard()
  const data = wizard.data

  function updateDisk(index: number, patch: Partial<{ size_gb: number; provisioning: 'thin' | 'thick' }>) {
    const disks = data.disks.map((disk, position) =>
      position === index ? { ...disk, ...patch } : disk,
    )
    wizard.update({ disks })
  }

  return (
    <section aria-label="VM hardware" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-800">Virtual machine hardware</h2>
        <p className="text-xs text-slate-500">
          Name, description, CPU, memory and virtual disks.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <FormRow label="VM name" htmlFor="vm-name" required error={wizard.errors['vm_name']}>
          <Input
            id="vm-name"
            placeholder="SERVER-PROD-001"
            value={data.vm_name}
            onChange={(event) => wizard.update({ vm_name: event.target.value.toUpperCase() })}
            className="font-mono"
          />
        </FormRow>

        <FormRow label="Description" htmlFor="vm-description">
          <Input
            id="vm-description"
            placeholder="Optional purpose / owner notes"
            value={data.description}
            maxLength={500}
            onChange={(event) => wizard.update({ description: event.target.value })}
          />
        </FormRow>

        <FormRow label="vCPU" htmlFor="vm-cpu" required error={wizard.errors['cpu']}>
          <Select
            id="vm-cpu"
            value={String(data.cpu)}
            onChange={(event) => wizard.update({ cpu: Number(event.target.value) })}
          >
            {[...new Set([...CPU_CHOICES, data.cpu])]
              .sort((a, b) => a - b)
              .map((value) => (
                <option key={value} value={value}>
                  {value} vCPU
                </option>
              ))}
          </Select>
        </FormRow>

        <FormRow label="Memory (GB)" htmlFor="vm-memory" required error={wizard.errors['memory_gb']}>
          <Select
            id="vm-memory"
            value={String(data.memory_gb)}
            onChange={(event) => wizard.update({ memory_gb: Number(event.target.value) })}
          >
            {[...new Set([...MEMORY_GB_CHOICES, data.memory_gb])]
              .sort((a, b) => a - b)
              .map((value) => (
                <option key={value} value={value}>
                  {value} GB
                </option>
              ))}
          </Select>
        </FormRow>

        <FormRow label="Firmware" htmlFor="vm-firmware">
          <Select
            id="vm-firmware"
            value={data.firmware}
            onChange={(event) => {
              const firmware = event.target.value as 'BIOS' | 'EFI'
              wizard.update({
                firmware,
                secure_boot: firmware === 'EFI' ? data.secure_boot : false,
              })
            }}
          >
            <option value="EFI">UEFI</option>
            <option value="BIOS">BIOS</option>
          </Select>
        </FormRow>

        <div className="flex items-end pb-4">
          <Checkbox
            label="Enable Secure Boot (UEFI only)"
            checked={data.secure_boot}
            disabled={data.firmware !== 'EFI'}
            onChange={(event) => wizard.update({ secure_boot: event.target.checked })}
          />
        </div>
      </div>

      {/* Disks */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="field-label mb-0">Virtual disks</p>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() =>
              wizard.update({
                disks: [...data.disks, { size_gb: 50, provisioning: 'thin', datastore_id: null }],
              })
            }
            disabled={data.disks.length >= 8}
          >
            <Plus className="h-3.5 w-3.5" /> Add disk
          </Button>
        </div>

        <div className="space-y-2">
          {data.disks.map((disk, index) => (
            <div
              key={index}
              className="flex flex-wrap items-end gap-3 rounded-md border border-slate-200 bg-surface-sunken px-3 py-2"
            >
              <span className="pb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Disk {index + 1}
              </span>
              <div className="w-28">
                <label className="field-label">Size (GB)</label>
                <Input
                  type="number"
                  min={1}
                  max={8000}
                  value={disk.size_gb}
                  aria-label={`Disk ${index + 1} size in GB`}
                  onChange={(event) => updateDisk(index, { size_gb: Number(event.target.value) })}
                />
              </div>
              <div className="w-36">
                <label className="field-label">Provisioning</label>
                <Select
                  value={disk.provisioning}
                  aria-label={`Disk ${index + 1} provisioning`}
                  onChange={(event) =>
                    updateDisk(index, { provisioning: event.target.value as 'thin' | 'thick' })
                  }
                >
                  <option value="thin">Thin</option>
                  <option value="thick">Thick</option>
                </Select>
              </div>
              {index > 0 && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="mb-0.5"
                  aria-label={`Remove disk ${index + 1}`}
                  onClick={() => wizard.update({ disks: data.disks.filter((_, position) => position !== index) })}
                >
                  <Trash2 className="h-4 w-4 text-red-500" />
                </Button>
              )}
            </div>
          ))}
        </div>
        {wizard.errors['disks'] && (
          <p className="mt-1 text-xs font-medium text-red-600">{wizard.errors['disks']}</p>
        )}
      </div>
    </section>
  )
}
