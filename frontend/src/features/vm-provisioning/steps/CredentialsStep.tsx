import { KeyRound, RefreshCw } from 'lucide-react'
import { useEffect } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, LoadingState } from '@/components/ui/feedback'
import { FormRow, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useProvisioningCredentials } from '@/features/vm-provisioning/hooks'

export function CredentialsStep() {
  const wizard = useWizard()
  const credentials = useProvisioningCredentials('guest_administrator')
  const items = credentials.data ?? []
  const guestAutomationDeferred = (
    wizard.data.source_type === 'blank' && !wizard.data.iso_id
  )

  useEffect(() => {
    if (
      credentials.isSuccess
      && wizard.data.guest_credential_secret_ref
      && !items.some((credential) => credential.name === wizard.data.guest_credential_secret_ref)
    ) {
      wizard.update({ guest_credential_secret_ref: '' })
    }
  }, [credentials.isSuccess, items, wizard.data.guest_credential_secret_ref, wizard.update])

  if (guestAutomationDeferred) {
    return (
      <section aria-label="Provisioning credentials" className="space-y-5">
        <header>
          <p className="console-kicker">Guest prerequisite</p>
          <h2>No guest credential is required yet</h2>
          <p>The deployment creates VM hardware only and stops before guest operations.</p>
        </header>
        <Alert tone="warning" title="Waiting for guest operating system">
          Install and boot an operating system before supplying credentials or enabling VMware Tools, guest networking, hostname, domain, certificate, or application operations.
        </Alert>
      </section>
    )
  }

  return (
    <section aria-label="Provisioning credentials" className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="console-kicker">Windows bootstrap</p>
          <h2>Provisioning administrator</h2>
          <p>Select the encrypted local administrator credential used until networking and domain join finish.</p>
        </div>
        <Badge tone="info"><RefreshCw className="h-3 w-3" /> Live credential catalog</Badge>
      </header>

      {credentials.isLoading ? (
        <LoadingState title="Loading credentials" description="Retrieving the latest encrypted credential revisions." />
      ) : credentials.isError ? (
        <Alert tone="danger" title="Credentials unavailable">
          The credential catalog could not be loaded.{' '}
          <Button size="sm" variant="secondary" onClick={() => void credentials.refetch()}>Retry</Button>
        </Alert>
      ) : items.length === 0 ? (
        <Alert tone="warning" title="No Windows provisioning administrator is configured">
          An administrator must create a credential with purpose “Windows provisioning administrator” before this deployment can continue.
        </Alert>
      ) : (
        <div className="console-group">
          <div className="console-group-header">
            <div>
              <p className="console-group-title">Bootstrap credential</p>
              <p className="console-group-description">Only the reference and revision are shown; values remain encrypted.</p>
            </div>
            <KeyRound className="h-4 w-4 text-slate-400" />
          </div>
          <div className="console-group-body">
            <FormRow label="Administrator credential" htmlFor="guest-credential" required error={wizard.errors.guest_credential_secret_ref}>
              <Select
                id="guest-credential"
                value={wizard.data.guest_credential_secret_ref}
                onChange={(event) => wizard.update({ guest_credential_secret_ref: event.target.value })}
              >
                <option value="">Select provisioning administrator</option>
                {items.map((credential) => (
                  <option key={credential.name} value={credential.name}>
                    {credential.name} · revision {credential.revision}
                  </option>
                ))}
              </Select>
            </FormRow>
            <Alert tone="info" title={wizard.data.source_type === 'blank' ? 'Used by unattended Windows Setup' : 'Used during first-boot provisioning'}>
              {wizard.data.source_type === 'blank'
                ? 'InfraOps uses this password in the generated unattended setup media, then authenticates through VMware Tools after Windows installation.'
                : 'InfraOps uses this account to complete guest networking and identity configuration before Active Directory authentication is available.'}
            </Alert>
          </div>
        </div>
      )}
    </section>
  )
}
