import { KeyRound, MonitorCog } from 'lucide-react'

import { Alert, Badge } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useTemplates } from '@/features/vm-provisioning/hooks'

export function OsStep() {
  const wizard = useWizard()
  const data = wizard.data
  const templates = useTemplates(data.vcenter_id, null, data.source_type === 'template')
  const selectedTemplate = templates.data?.find((template) => template.id === data.template_id)

  if (data.source_type === 'blank') {
    return (
      <section aria-label="Operating system" className="space-y-5">
        <header>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Guest operating system</p>
          <h2>OS installation handoff</h2>
          <p>Blank VM creation does not install or customize a guest operating system.</p>
        </header>
        <div className="console-group">
          <div className="console-group-header">
            <div>
              <p className="console-group-title">Resulting VM state</p>
              <p className="console-group-description">The infrastructure object is ready for installation media.</p>
            </div>
            <MonitorCog className="h-4 w-4 text-slate-400" aria-hidden />
          </div>
          <div className="console-group-body">
            <Alert tone="warning" title="Powered off with no operating system">
              InfraOps creates empty virtual disks. Install the OS and VMware Tools before configuring guest
              networking, certificates, or applications.
            </Alert>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section aria-label="Operating system configuration" className="space-y-5">
      <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Guest operating system</p>
            <h2>Guest customization</h2>
            <p>The OS is inherited from the selected template. Configure only supported guest overrides.</p>
          </div>
          {selectedTemplate?.os_version && <Badge tone="info">{selectedTemplate.os_version}</Badge>}
        </div>
      </header>

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Template-derived operating system</p>
            <p className="console-group-description">Read-only source information.</p>
          </div>
          <MonitorCog className="h-4 w-4 text-slate-400" aria-hidden />
        </div>
        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
          <span className="text-sm font-semibold text-slate-900">{selectedTemplate?.name ?? 'Selected template'}</span>
          {selectedTemplate?.os_version && <Badge tone="neutral">{selectedTemplate.os_version}</Badge>}
          {selectedTemplate && selectedTemplate.os_family !== 'windows' && (
            <Badge tone="warning">Limited guest automation</Badge>
          )}
        </div>
      </div>

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Guest identity</p>
            <p className="console-group-description">Applied through VMware Tools after first boot.</p>
          </div>
        </div>
        <div className="console-group-body grid grid-cols-1 gap-x-4 md:grid-cols-2">
          <FormRow label="Computer name" htmlFor="hostname" hint="Defaults to the VM inventory name." error={wizard.errors.hostname}>
            <Input
              id="hostname"
              className="font-mono"
              placeholder={data.vm_name || 'SERVER-PROD-001'}
              value={data.hostname}
              onChange={(event) => wizard.update({ hostname: event.target.value.toUpperCase() })}
            />
          </FormRow>
          <FormRow label="Windows time zone" htmlFor="timezone" hint="Optional Windows time-zone identifier.">
            <Input id="timezone" placeholder="W. Europe Standard Time" value={data.timezone} onChange={(event) => wizard.update({ timezone: event.target.value })} />
          </FormRow>
        </div>
      </div>

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Active Directory</p>
            <p className="console-group-description">Optional domain join after network configuration.</p>
          </div>
          <KeyRound className="h-4 w-4 text-slate-400" aria-hidden />
        </div>
        <div className="console-group-body space-y-4">
          <Checkbox
            label="Join an Active Directory domain"
            checked={data.domain_join.enabled}
            onChange={(event) => wizard.update({ domain_join: { ...data.domain_join, enabled: event.target.checked } })}
          />
          {data.domain_join.enabled && (
            <div className="grid grid-cols-1 gap-x-4 border-t border-slate-200 pt-4 md:grid-cols-2">
              <FormRow label="Domain" htmlFor="join-domain" required error={wizard.errors['domain_join.domain']}>
                <Input id="join-domain" placeholder="ad.company.local" value={data.domain_join.domain} onChange={(event) => wizard.update({ domain_join: { ...data.domain_join, domain: event.target.value } })} />
              </FormRow>
              <FormRow label="Organizational unit (DN)" htmlFor="join-ou">
                <Input id="join-ou" placeholder="OU=Servers,DC=ad,DC=company,DC=local" value={data.domain_join.ou} onChange={(event) => wizard.update({ domain_join: { ...data.domain_join, ou: event.target.value } })} />
              </FormRow>
              <FormRow label="Credential reference" htmlFor="join-secret" hint="Managed under Administration → Credentials." error={wizard.errors['domain_join.credential_secret_ref']}>
                <Select
                  id="join-secret"
                  value={data.domain_join.credential_secret_ref}
                  onChange={(event) => wizard.update({ domain_join: { ...data.domain_join, credential_secret_ref: event.target.value } })}
                >
                  <option value="domain-join">domain-join</option>
                </Select>
              </FormRow>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
