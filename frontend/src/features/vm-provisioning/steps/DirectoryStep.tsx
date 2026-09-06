import { KeyRound } from 'lucide-react'
import { useEffect } from 'react'

import { Alert, LoadingState } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useProvisioningCredentials } from '@/features/vm-provisioning/hooks'
import { deriveGuestIdentity } from '@/features/vm-provisioning/identity'

export function DirectoryStep() {
  const wizard = useWizard()
  const data = wizard.data
  const credentials = useProvisioningCredentials('domain_join')
  const items = credentials.data ?? []
  const identity = deriveGuestIdentity(data.vm_name, data.hostname, data.domain_join.domain)

  useEffect(() => {
    if (
      credentials.isSuccess
      && data.domain_join.credential_secret_ref
      && !items.some((credential) => credential.name === data.domain_join.credential_secret_ref)
    ) {
      wizard.update({
        domain_join: { ...data.domain_join, credential_secret_ref: '' },
      })
    }
  }, [credentials.isSuccess, data.domain_join, items, wizard.update])

  return (
    <section aria-label="Active Directory" className="space-y-5">
      <header>
        <p className="console-kicker">After network configuration</p>
        <h2>Active Directory</h2>
        <p>Join the installed Windows guest only after its requested network and DNS configuration is active.</p>
      </header>
      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Domain membership</p>
            <p className="console-group-description">Uses a separate least-privilege domain credential.</p>
          </div>
          <KeyRound className="h-4 w-4 text-slate-400" />
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
                <Input id="join-domain" placeholder="ad.company.local" value={data.domain_join.domain} onChange={(event) => wizard.update({ domain_join: { ...data.domain_join, domain: event.target.value.toLowerCase() } })} />
              </FormRow>
              <FormRow label="Organizational unit (DN)" htmlFor="join-ou">
                <Input id="join-ou" placeholder="OU=Servers,DC=ad,DC=company,DC=local" value={data.domain_join.ou} onChange={(event) => wizard.update({ domain_join: { ...data.domain_join, ou: event.target.value } })} />
              </FormRow>
              <FormRow label="Domain-join credential" htmlFor="join-secret" required error={wizard.errors['domain_join.credential_secret_ref']}>
                {credentials.isLoading ? <LoadingState title="Loading credentials" /> : (
                  <Select id="join-secret" value={data.domain_join.credential_secret_ref} onChange={(event) => wizard.update({ domain_join: { ...data.domain_join, credential_secret_ref: event.target.value } })}>
                    <option value="">Select domain-join credential</option>
                    {items.map((credential) => <option key={credential.name} value={credential.name}>{credential.name} · revision {credential.revision}</option>)}
                  </Select>
                )}
              </FormRow>
              <div className="md:col-span-2">
                <Alert tone="info" title="Effective fully qualified DNS name">
                  {identity.fqdn ?? 'Enter the Active Directory domain to preview the resulting FQDN.'}
                </Alert>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
