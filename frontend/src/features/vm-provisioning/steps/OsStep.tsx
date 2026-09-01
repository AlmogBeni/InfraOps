import { Alert, Badge } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useTemplates } from '@/features/vm-provisioning/hooks'

export function OsStep() {
  const wizard = useWizard()
  const data = wizard.data
  const templates = useTemplates(
    data.vcenter_id,
    data.datacenter_id,
    data.source_type === 'template',
  )
  const selectedTemplate = templates.data?.find((template) => template.id === data.template_id)

  if (data.source_type === 'blank') {
    return (
      <section aria-label="Operating system" className="space-y-4">
        <header>
          <h2 className="text-sm font-semibold text-slate-900">Operating system</h2>
          <p className="mt-1 text-xs text-slate-500">No operating system is installed by this workflow.</p>
        </header>
        <Alert tone="info" title="Blank virtual machine">
          InfraOps will create empty virtual disks and leave the VM powered off. Install the operating system
          and VMware Tools before configuring guest networking, certificates or applications.
        </Alert>
      </section>
    )
  }

  return (
    <section aria-label="Operating system configuration" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-900">Operating system customization</h2>
        <p className="mt-1 text-xs text-slate-500">
          The infrastructure template was selected earlier. Configure supported guest settings here.
        </p>
      </header>

      <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Template-derived OS</p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-slate-900">
            {selectedTemplate?.name ?? 'Selected template'}
          </span>
          {selectedTemplate?.os_version && <Badge tone="info">{selectedTemplate.os_version}</Badge>}
          {selectedTemplate && selectedTemplate.os_family !== 'windows' && (
            <Badge tone="warning">Guest automation may be unavailable</Badge>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <FormRow
          label="Computer name (hostname)"
          htmlFor="hostname"
          hint="Defaults to the VM name when left empty."
          error={wizard.errors.hostname}
        >
          <Input
            id="hostname"
            className="font-mono"
            placeholder={data.vm_name || 'SERVER-PROD-001'}
            value={data.hostname}
            onChange={(event) => wizard.update({ hostname: event.target.value.toUpperCase() })}
          />
        </FormRow>

        <FormRow label="Time zone" htmlFor="timezone" hint="Windows time zone name (optional).">
          <Input
            id="timezone"
            placeholder="W. Europe Standard Time"
            value={data.timezone}
            onChange={(event) => wizard.update({ timezone: event.target.value })}
          />
        </FormRow>
      </div>

      <details className="rounded-md border border-slate-200 px-3 py-2" open={data.domain_join.enabled}>
        <summary className="cursor-pointer select-none text-sm font-medium text-slate-700">
          Domain join (optional)
        </summary>
        <div className="mt-3 space-y-3">
          <Checkbox
            label="Join an Active Directory domain after first boot"
            checked={data.domain_join.enabled}
            onChange={(event) =>
              wizard.update({ domain_join: { ...data.domain_join, enabled: event.target.checked } })
            }
          />
          {data.domain_join.enabled && (
            <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
              <FormRow
                label="Domain"
                htmlFor="join-domain"
                required
                error={wizard.errors['domain_join.domain']}
              >
                <Input
                  id="join-domain"
                  placeholder="ad.company.local"
                  value={data.domain_join.domain}
                  onChange={(event) =>
                    wizard.update({ domain_join: { ...data.domain_join, domain: event.target.value } })
                  }
                />
              </FormRow>
              <FormRow label="Organizational unit (DN)" htmlFor="join-ou">
                <Input
                  id="join-ou"
                  placeholder="OU=Servers,DC=ad,DC=company,DC=local"
                  value={data.domain_join.ou}
                  onChange={(event) =>
                    wizard.update({ domain_join: { ...data.domain_join, ou: event.target.value } })
                  }
                />
              </FormRow>
              <FormRow
                label="Credential reference"
                htmlFor="join-secret"
                hint="Logical secret reference managed under Administration → Credentials."
                error={wizard.errors['domain_join.credential_secret_ref']}
              >
                <Select
                  id="join-secret"
                  value={data.domain_join.credential_secret_ref}
                  onChange={(event) =>
                    wizard.update({
                      domain_join: {
                        ...data.domain_join,
                        credential_secret_ref: event.target.value,
                      },
                    })
                  }
                >
                  <option value="domain-join">domain-join</option>
                </Select>
              </FormRow>
            </div>
          )}
        </div>
      </details>
    </section>
  )
}
