import { MonitorCog } from 'lucide-react'

import { Badge } from '@/components/ui/feedback'
import { FormRow, Input, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useIsos } from '@/features/vm-provisioning/hooks'
import { deriveGuestIdentity } from '@/features/vm-provisioning/identity'
import { WINDOWS_EDITIONS } from '@/features/vm-provisioning/schema'

export function OsStep({ embedded = false }: { embedded?: boolean }) {
  const wizard = useWizard()
  const data = wizard.data
  const isos = useIsos(data.vcenter_id, data.datacenter_id)
  const selectedIso = isos.data?.find((image) => image.id === data.iso_id)
  const identity = deriveGuestIdentity(
    data.vm_name,
    data.hostname,
    data.domain_join.enabled ? data.domain_join.domain : null,
  )

  return (
    <section aria-label="Operating system configuration" className="space-y-5">
      {!embedded && (
        <header>
          <p className="console-kicker">Guest operating system</p>
          <h2>Windows identity</h2>
          <p>Windows Server is installed unattended from the selected ISO.</p>
        </header>
      )}

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Windows source</p>
            <p className="console-group-description">The selected media supplies the base operating system.</p>
          </div>
          <MonitorCog className="h-4 w-4 text-slate-400" aria-hidden />
        </div>
        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
          <span className="text-sm font-semibold text-slate-900">
            {selectedIso?.name ?? 'Windows Server installation ISO'}
          </span>
          <Badge tone="neutral">Unattended install</Badge>
        </div>
      </div>

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Guest identity</p>
            <p className="console-group-description">Applied after Windows Setup and before optional domain join.</p>
          </div>
        </div>
        <div className="console-group-body grid grid-cols-1 gap-x-4 md:grid-cols-2">
          <FormRow
            label="Computer name"
            htmlFor="hostname"
            hint={data.domain_join.enabled
              ? 'Domain-joined guests use the VM inventory name as the short Windows computer name.'
              : 'Defaults to the VM inventory name.'}
            error={wizard.errors.hostname}
          >
            <Input
              id="hostname"
              className="font-mono"
              placeholder={data.vm_name || 'SERVER-PROD-001'}
              value={data.domain_join.enabled ? identity.computerName : data.hostname}
              disabled={data.domain_join.enabled}
              onChange={(event) => wizard.update({ hostname: event.target.value.toUpperCase() })}
            />
          </FormRow>
          <FormRow label="Windows time zone" htmlFor="timezone" hint="Optional Windows time-zone identifier used by unattended setup.">
            <Input id="timezone" placeholder="Israel Standard Time" value={data.timezone} onChange={(event) => wizard.update({ timezone: event.target.value })} />
          </FormRow>
          <FormRow
            label="Windows language / locale"
            htmlFor="installation-locale"
            hint="Language tag available in the selected ISO, such as en-US."
          >
            <Input id="installation-locale" value={data.installation_locale} onChange={(event) => wizard.update({ installation_locale: event.target.value })} />
          </FormRow>
          <FormRow label="Keyboard input locale" htmlFor="input-locale" hint="Windows input locale, for example 0409:00000409.">
            <Input id="input-locale" className="font-mono" value={data.input_locale} onChange={(event) => wizard.update({ input_locale: event.target.value })} />
          </FormRow>
          <FormRow
            label="Windows Server edition"
            htmlFor="windows-edition"
            hint="Always installed with the Desktop Experience (full graphical interface)."
            error={wizard.errors.windows_image_index}
          >
            <Select
              id="windows-edition"
              value={String(data.windows_image_index)}
              onChange={(event) => wizard.update({ windows_image_index: Number(event.target.value) })}
            >
              {WINDOWS_EDITIONS.map((edition) => (
                <option key={edition.index} value={edition.index}>{edition.label}</option>
              ))}
            </Select>
          </FormRow>
        </div>
      </div>
    </section>
  )
}
