import { Alert, Spinner } from '@/components/ui/feedback'
import { Checkbox } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useCertificatePackages } from '@/features/vm-provisioning/hooks'

export function CertificatesStep() {
  const wizard = useWizard()
  const data = wizard.data
  const packages = useCertificatePackages()

  if (data.source_type === 'blank') {
    return (
      <section aria-label="Certificate selection" className="space-y-4">
        <header><h2 className="text-sm font-semibold text-slate-900">Certificates</h2></header>
        <Alert tone="info" title="Available after OS installation">
          Certificate deployment requires a running guest with VMware Tools. No certificate material will be
          submitted with this blank VM request.
        </Alert>
      </section>
    )
  }

  function togglePackage(packageId: string, checked: boolean) {
    const set = new Set(data.certificate_package_ids)
    if (checked) set.add(packageId)
    else set.delete(packageId)
    wizard.update({ certificate_package_ids: [...set] })
  }

  return (
    <section aria-label="Certificate selection" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-800">Corporate certificates</h2>
        <p className="text-xs text-slate-500">
          Selected packages are installed into the Local Computer Trusted Root and Intermediate stores.
          Already-present certificates are skipped automatically.
        </p>
      </header>

      {packages.isLoading ? (
        <div className="flex h-24 items-center justify-center">
          <Spinner />
        </div>
      ) : (packages.data ?? []).length === 0 ? (
        <p className="text-sm text-slate-500">No certificate packages have been published yet.</p>
      ) : (
        <ul className="space-y-2">
          {(packages.data ?? []).map((package_) => (
            <li key={package_.id} className="rounded-md border border-slate-200 px-3 py-2">
              <Checkbox
                label={
                  <span>
                    <span className="font-medium">{package_.name}</span>
                    {package_.description && (
                      <span className="block text-xs text-slate-500">{package_.description}</span>
                    )}
                  </span>
                }
                checked={data.certificate_package_ids.includes(package_.id)}
                onChange={(event) => togglePackage(package_.id, event.target.checked)}
              />
              {data.certificate_package_ids.includes(package_.id) && package_.certificates.length > 0 && (
                <ul className="ml-6 mt-1 space-y-0.5 border-l border-slate-200 pl-3">
                  {package_.certificates.map((cert) => (
                    <li key={cert.id} className="text-xs text-slate-500">
                      {cert.friendly_name} ·{' '}
                      <span className="font-mono">{cert.fingerprint_sha256.slice(0, 16)}…</span> ·{' '}
                      {cert.destination_store === 'Root' ? 'Trusted Root' : 'Intermediate'}
                      {cert.not_after && ` · expires ${new Date(cert.not_after).toLocaleDateString()}`}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
