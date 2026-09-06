import { Boxes } from 'lucide-react'
import { useEffect } from 'react'

import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { Checkbox } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useApplications } from '@/features/vm-provisioning/hooks'

export function ApplicationsStep({ embedded = false }: { embedded?: boolean }) {
  const wizard = useWizard()
  const data = wizard.data
  const applications = useApplications()

  useEffect(() => {
    if (!applications.isSuccess || data.application_ids.length === 0) return
    const available = new Set(applications.data.map((application) => application.id))
    const validSelections = data.application_ids.filter((id) => available.has(id))
    if (validSelections.length !== data.application_ids.length) {
      wizard.update({ application_ids: validSelections })
    }
  }, [applications.data, applications.isSuccess, data.application_ids, wizard.update])

  function toggleApplication(applicationId: string, checked: boolean) {
    const selected = new Set(data.application_ids)
    if (checked) selected.add(applicationId)
    else selected.delete(applicationId)
    wizard.update({ application_ids: [...selected] })
  }

  return (
    <section aria-label="Application selection" className="space-y-5">
      {!embedded && <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Guest software</p>
            <h2>Approved applications</h2>
            <p>Select catalog entries to install after guest configuration and certificate deployment.</p>
          </div>
          <Badge tone="neutral">{data.application_ids.length} selected</Badge>
        </div>
      </header>}

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Application catalog</p>
            <p className="console-group-description">Dependencies are resolved and installed in policy-defined order.</p>
          </div>
          <Boxes className="h-4 w-4 text-slate-400" aria-hidden />
        </div>

        {applications.isLoading ? (
          <div className="flex h-24 items-center justify-center gap-2 text-xs text-slate-500">
            <Spinner /> Loading application catalog…
          </div>
        ) : applications.isError ? (
          <div className="p-4">
            <Alert tone="danger" title="Application catalog unavailable">
              {applications.error instanceof Error ? applications.error.message : 'Applications could not be loaded.'}
            </Alert>
          </div>
        ) : (applications.data ?? []).length === 0 ? (
          <EmptyState title="The application catalog is empty." description="Administrators can publish approved installers under Application Catalog." />
        ) : (
          <ul className="divide-y divide-slate-200">
            {(applications.data ?? []).map((application) => {
              const selected = data.application_ids.includes(application.id)
              return (
                <li key={application.id} className={`px-4 py-3 ${selected ? 'bg-brand-50/50' : 'bg-white'}`}>
                  <Checkbox
                    label={
                      <span className="block">
                        <span className="font-semibold text-slate-900">{application.name}</span>{' '}
                        {application.version && <Badge tone="neutral">v{application.version}</Badge>}{' '}
                        {application.reboot_required && <Badge tone="warning">Reboot required</Badge>}
                        {application.description && <span className="mt-0.5 block text-xs text-slate-500">{application.description}</span>}
                        <span className="mt-1 block font-mono text-[10px] uppercase tracking-wide text-slate-500">
                          {application.installer_type} · {application.dependency_ids.length} dependenc{application.dependency_ids.length === 1 ? 'y' : 'ies'} · timeout {application.timeout_seconds}s
                        </span>
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
      </div>
    </section>
  )
}
