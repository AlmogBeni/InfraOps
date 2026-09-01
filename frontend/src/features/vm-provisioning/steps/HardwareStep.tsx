import { Cpu, HardDrive, Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'

const CPU_CHOICES = [1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64]
const MEMORY_GB_CHOICES = [2, 4, 8, 16, 24, 32, 48, 64, 96, 128, 256]

export function HardwareStep({ embedded = false }: { embedded?: boolean }) {
  const wizard = useWizard()
  const data = wizard.data
  const totalDisk = data.disks.reduce((total, disk) => total + disk.size_gb, 0)

  function updateDisk(index: number, patch: Partial<{ size_gb: number; provisioning: 'thin' | 'thick' }>) {
    wizard.update({
      disks: data.disks.map((disk, position) => position === index ? { ...disk, ...patch } : disk),
    })
  }

  return (
    <section aria-label="Compute configuration" className="space-y-5">
      {!embedded && <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Virtual hardware</p>
            <h2>Compute and identity</h2>
            <p>Define the inventory identity and requested virtual hardware.</p>
          </div>
          <Badge tone="neutral">{data.cpu} vCPU · {data.memory_gb} GB · {totalDisk} GB</Badge>
        </div>
      </header>}

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Identity and sizing</p>
            <p className="console-group-description">VM inventory name, compute allocation, and boot firmware.</p>
          </div>
          <Cpu className="h-4 w-4 text-slate-400" aria-hidden />
        </div>
        <div className="console-group-body grid grid-cols-1 gap-x-4 md:grid-cols-2">
          <FormRow label="VM name" htmlFor="vm-name" required error={wizard.errors.vm_name}>
            <Input
              id="vm-name"
              placeholder="SERVER-PROD-001"
              value={data.vm_name}
              onChange={(event) => wizard.update({ vm_name: event.target.value.toUpperCase() })}
              className="font-mono"
            />
          </FormRow>

          <FormRow label="Description" htmlFor="vm-description" hint="Optional owner, service, or purpose note.">
            <Input
              id="vm-description"
              placeholder="Payment API · Platform Engineering"
              value={data.description}
              maxLength={500}
              onChange={(event) => wizard.update({ description: event.target.value })}
            />
          </FormRow>

          <FormRow label="vCPU" htmlFor="vm-cpu" required error={wizard.errors.cpu}>
            <Select id="vm-cpu" value={String(data.cpu)} onChange={(event) => wizard.update({ cpu: Number(event.target.value) })}>
              {[...new Set([...CPU_CHOICES, data.cpu])].sort((a, b) => a - b).map((value) => (
                <option key={value} value={value}>{value} vCPU</option>
              ))}
            </Select>
          </FormRow>

          <FormRow label="Memory" htmlFor="vm-memory" required error={wizard.errors.memory_gb}>
            <Select id="vm-memory" value={String(data.memory_gb)} onChange={(event) => wizard.update({ memory_gb: Number(event.target.value) })}>
              {[...new Set([...MEMORY_GB_CHOICES, data.memory_gb])].sort((a, b) => a - b).map((value) => (
                <option key={value} value={value}>{value} GB RAM</option>
              ))}
            </Select>
          </FormRow>

          <FormRow label="Firmware" htmlFor="vm-firmware">
            <Select
              id="vm-firmware"
              value={data.firmware}
              onChange={(event) => {
                const firmware = event.target.value as 'BIOS' | 'EFI'
                wizard.update({ firmware, secure_boot: firmware === 'EFI' ? data.secure_boot : false })
              }}
            >
              <option value="EFI">UEFI</option>
              <option value="BIOS">Legacy BIOS</option>
            </Select>
          </FormRow>

          <div className="flex items-center pb-4 md:pt-5">
            <Checkbox
              label="Enable Secure Boot"
              checked={data.secure_boot}
              disabled={data.firmware !== 'EFI'}
              onChange={(event) => wizard.update({ secure_boot: event.target.checked })}
            />
          </div>
        </div>
      </div>

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Virtual disks</p>
            <p className="console-group-description">Maximum eight disks. Capacity is revalidated before creation.</p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => wizard.update({ disks: [...data.disks, { size_gb: 50, provisioning: 'thin', datastore_id: null }] })}
            disabled={data.disks.length >= 8}
          >
            <Plus className="h-3.5 w-3.5" /> Add disk
          </Button>
        </div>
        <div className="divide-y divide-slate-200">
          {data.disks.map((disk, index) => (
            <div key={index} className="grid grid-cols-1 items-end gap-3 px-4 py-3 sm:grid-cols-[100px_140px_170px_1fr_auto]">
              <div className="flex items-center gap-2 self-center">
                <HardDrive className="h-4 w-4 text-slate-400" />
                <span className="text-xs font-semibold text-slate-700">Disk {index + 1}</span>
              </div>
              <div>
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
              <div>
                <label className="field-label">Provisioning</label>
                <Select
                  value={disk.provisioning}
                  aria-label={`Disk ${index + 1} provisioning`}
                  onChange={(event) => updateDisk(index, { provisioning: event.target.value as 'thin' | 'thick' })}
                >
                  <option value="thin">Thin provisioned</option>
                  <option value="thick">Thick provisioned</option>
                </Select>
              </div>
              <p className="self-center text-xs text-slate-500">Datastore assigned in the Storage phase.</p>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={index === 0}
                aria-label={`Remove disk ${index + 1}`}
                onClick={() => wizard.update({ disks: data.disks.filter((_, position) => position !== index) })}
              >
                <Trash2 className="h-4 w-4 text-red-500" />
              </Button>
            </div>
          ))}
        </div>
        {wizard.errors.disks && (
          <p className="border-t border-red-100 bg-red-50 px-4 py-2 text-xs font-medium text-red-700">{wizard.errors.disks}</p>
        )}
      </div>
    </section>
  )
}
