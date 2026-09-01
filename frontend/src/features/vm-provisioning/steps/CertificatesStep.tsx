import { ShieldCheck } from 'lucide-react'

import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { Checkbox } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useCertificatePackages } from '@/features/vm-provisioning/hooks'

export function CertificatesStep() {
  const wizard = useWizard()
  const data = wizard.data
  const packages = useCertificatePackages()

  if (data.source_type === 'blank') {
    return (
      <section aria-label="Certificate selection" className="space-y-5">
        <header>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Guest security</p>
          <h2>Certificate deployment</h2>
          <p>Certificate installation requires a running guest with VMware Tools.</p>
        </header>
        <Alert tone="info" title="Not applicable to a blank VM">
          No certificate package IDs or certificate material will be included in this request.
        </Alert>
      </section>
    )
  }

  function togglePackage(packageId: string, checked: boolean) {
    const selected = new Set(data.certificate_package_ids)
    if (checked) selected.add(packageId)
    else selected.delete(packageId)
    wizard.update({ certificate_package_ids: [...selected] })
  }

  return (
    <section aria-label="Certificate selection" className="space-y-5">
      <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Guest security</p>
            <h2>Certificate packages</h2>
            <p>Select approved public certificate bundles for Local Computer trust stores.</p>
          </div>
          <Badge tone="neutral">{data.certificate_package_ids.length} selected</Badge>
        </div>
      </header>

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Published packages</p>
            <p className="console-group-description">Existing thumbprints are detected and skipped automatically.</p>
          </div>
          <ShieldCheck className="h-4 w-4 text-slate-400" aria-hidden />
        </div>

        {packages.isLoading ? (
          <div className="flex h-24 items-center justify-center gap-2 text-xs text-slate-500">
            <Spinner /> Loading certificate packages…
          </div>
        ) : packages.isError ? (
          <div className="p-4">
            <Alert tone="danger" title="Certificate catalog unavailable">
              {packages.error instanceof Error ? packages.error.message : 'Packages could not be loaded.'}
            </Alert>
          </div>
        ) : (packages.data ?? []).length === 0 ? (
          <EmptyState title="No certificate packages have been published." description="An administrator can register public certificates under Certificate Packages." />
        ) : (
          <ul className="divide-y divide-slate-200">
            {(packages.data ?? []).map((package_) => {
              const selected = data.certificate_package_ids.includes(package_.id)
              return (
                <li key={package_.id} className={selected ? 'bg-brand-50/50' : 'bg-white'}>
                  <div className="px-4 py-3">
                    <Checkbox
                      label={
                        <span>
                          <span className="font-semibold text-slate-900">{package_.name}</span>
                          <Badge tone="neutral" className="ml-2">{package_.certificates.length} certificate(s)</Badge>
                          {package_.description && <span className="mt-0.5 block text-xs text-slate-500">{package_.description}</span>}
                        </span>
                      }
                      checked={selected}
                      onChange={(event) => togglePackage(package_.id, event.target.checked)}
                    />
                    {selected && package_.certificates.length > 0 && (
                      <div className="ml-6 mt-2 overflow-hidden rounded border border-slate-200 bg-white">
                        {package_.certificates.map((certificate) => (
                          <div key={certificate.id} className="grid grid-cols-1 gap-1 border-b border-slate-100 px-3 py-2 text-[11px] last:border-b-0 sm:grid-cols-[minmax(0,1fr)_120px_160px]">
                            <span className="font-medium text-slate-700">{certificate.friendly_name}</span>
                            <span className="text-slate-500">{certificate.destination_store === 'Root' ? 'Trusted Root' : 'Intermediate'}</span>
                            <span className="font-mono text-slate-500">{certificate.fingerprint_sha256.slice(0, 16)}…</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}
