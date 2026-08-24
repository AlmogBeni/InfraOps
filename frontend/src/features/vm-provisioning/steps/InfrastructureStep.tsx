import { useEffect } from 'react'

import { Alert, Spinner } from '@/components/ui/feedback'
import { FormRow, Select } from '@/components/ui/form-controls'
import { useDatacenters, useSites, useVcenters } from '@/features/vm-provisioning/hooks'
import { useWizard } from '@/features/vm-provisioning/context'

export function InfrastructureStep() {
  const wizard = useWizard()
  const data = wizard.data

  const vcenters = useVcenters()
  const sites = useSites()
  const datacenters = useDatacenters(data.vcenter_id)

  // Reset downstream selections when the vCenter changes.
  useEffect(() => {
    if (!data.vcenter_id) return
    wizard.update({ site_id: '', datacenter_id: '', cluster_id: '' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.vcenter_id])

  const selectedSite = sites.data?.find((site) => site.id === data.site_id)

  return (
    <section aria-label="Infrastructure selection" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-800">Target infrastructure</h2>
        <p className="text-xs text-slate-500">
          Choose the vCenter, site and datacenter. Options are retrieved live from the configured connections.
        </p>
      </header>

      {vcenters.isLoading || sites.isLoading ? (
        <div className="flex h-24 items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <>
          <FormRow label="vCenter" htmlFor="vcenter" required error={wizard.errors['vcenter_id']}>
            <Select
              id="vcenter"
              value={data.vcenter_id}
              onChange={(event) => wizard.update({ vcenter_id: event.target.value })}
            >
              <option value="">— Select a vCenter —</option>
              {(vcenters.data ?? [])
                .filter((entry) => entry.enabled)
                .map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name} ({entry.host})
                  </option>
                ))}
            </Select>
          </FormRow>

          <FormRow label="Site" htmlFor="site" required error={wizard.errors['site_id']}>
            <Select
              id="site"
              value={data.site_id}
              onChange={(event) => wizard.update({ site_id: event.target.value })}
              disabled={!data.vcenter_id}
            >
              <option value="">— Select a site —</option>
              {(sites.data ?? [])
                .filter((site) => !data.vcenter_id || site.vcenter_id === data.vcenter_id)
                .filter((site) => site.enabled)
                .map((site) => (
                  <option key={site.id} value={site.id}>
                    {site.name}
                    {site.description ? ` — ${site.description}` : ''}
                  </option>
                ))}
            </Select>
          </FormRow>

          <FormRow
            label="Datacenter"
            htmlFor="datacenter"
            required
            error={wizard.errors['datacenter_id']}
          >
            <Select
              id="datacenter"
              value={data.datacenter_id}
              onChange={(event) => wizard.update({ datacenter_id: event.target.value })}
              disabled={!data.vcenter_id}
            >
              <option value="">— Select a datacenter —</option>
              {(datacenters.data ?? []).map((dc) => (
                <option key={dc.id} value={dc.id}>
                  {dc.name}
                </option>
              ))}
            </Select>
          </FormRow>

          {selectedSite && datacenters.isFetching && (
            <Alert tone="info">Loading datacenters from {selectedSite.name}…</Alert>
          )}
        </>
      )}
    </section>
  )
}
