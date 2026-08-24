import { Badge, Spinner } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select } from '@/components/ui/form-controls'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { useWizard } from '@/features/vm-provisioning/context'
import { useTemplates } from '@/features/vm-provisioning/hooks'

export function OsStep() {
  const wizard = useWizard()
  const data = wizard.data
  const templates = useTemplates(data.vcenter_id, data.datacenter_id)

  return (
    <section aria-label="Operating system selection" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-800">Operating system</h2>
        <p className="text-xs text-slate-500">
          Provisioning is template-based — pick a corporate image retrieved live from vCenter.
        </p>
      </header>

      {templates.isLoading ? (
        <div className="flex h-24 items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th />
                <Th>Template</Th>
                <Th>OS</Th>
                <Th>Last modified</Th>
                <Th>Description</Th>
              </tr>
            </thead>
            <tbody>
              {(templates.data ?? []).map((template) => (
                <Tr key={template.id} clickable onClick={() => wizard.update({ template_id: template.id })}>
                  <Td>
                    <input
                      type="radio"
                      name="template-select"
                      aria-label={`Select ${template.name}`}
                      checked={data.template_id === template.id}
                      onChange={() => wizard.update({ template_id: template.id })}
                      className="h-4 w-4 border-slate-300 text-brand-600 focus:ring-brand-500"
                    />
                  </Td>
                  <Td className="font-medium text-slate-800">
                    {template.name}{' '}
                    {template.os_family !== 'windows' && <Badge tone="warning">Non-Windows</Badge>}
                  </Td>
                  <Td>{template.os_version}</Td>
                  <Td>{template.last_modified ? new Date(template.last_modified).toLocaleDateString() : '—'}</Td>
                  <Td className="max-w-xs truncate text-slate-500">{template.description}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
          {wizard.errors['template_id'] && (
            <p className="text-xs font-medium text-red-600">{wizard.errors['template_id']}</p>
          )}
        </>
      )}

      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <FormRow
          label="Computer name (hostname)"
          htmlFor="hostname"
          hint="Defaults to the VM name when left empty."
          error={wizard.errors['hostname']}
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
              wizard.update({
                domain_join: { ...data.domain_join, enabled: event.target.checked },
              })
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
                    wizard.update({
                      domain_join: { ...data.domain_join, domain: event.target.value },
                    })
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
