import { Badge, Spinner } from '@/components/ui/feedback'
import { Checkbox } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useApplications } from '@/features/vm-provisioning/hooks'

export function ApplicationsStep() {
  const wizard = useWizard()
  const data = wizard.data
  const applications = useApplications()

  function toggleApplication(applicationId: string, checked: boolean) {
    const set = new Set(data.application_ids)
    if (checked) set.add(applicationId)
    else set.delete(applicationId)
    wizard.update({ application_ids: [...set] })
  }

  return (
    <section aria-label="Application selection" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-800">Approved applications</h2>
        <p className="text-xs text-slate-500">
          Only administrator-approved packages are listed. Dependencies are installed automatically in the
          correct order; already-installed applications are skipped.
        </p>
      </header>

      {applications.isLoading ? (
        <div className="flex h-24 items-center justify-center">
          <Spinner />
        </div>
      ) : (applications.data ?? []).length === 0 ? (
        <p className="text-sm text-slate-500">The application catalog is empty.</p>
      ) : (
        <ul className="grid grid-cols-1 gap-2 lg:grid-cols-2">
          {(applications.data ?? []).map((application) => {
            const selected = data.application_ids.includes(application.id)
            return (
              <li key={application.id} className="rounded-md border border-slate-200 px-3 py-2">
                <Checkbox
                  label={
                    <span>
                      <span className="font-medium">{application.name}</span>{' '}
                      {application.version && (
                        <Badge tone="neutral">v{application.version}</Badge>
                      )}
                      {application.reboot_required && <Badge tone="warning">reboot</Badge>}
                      {application.description && (
                        <span className="block text-xs text-slate-500">{application.description}</span>
                      )}
                      {application.dependency_ids.length > 0 && (
                        <span className="block text-[11px] text-slate-400">
                          Requires {application.dependency_ids.length} dependency(ies) — resolved automatically.
                        </span>
                      )}
                    </span>
                  }
                  checked={selected}
                  onChange={(event) => toggleApplication(application.id, event.target.checked)}
                />
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
