import { useMutation } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, CheckStatusBadge, Spinner } from '@/components/ui/feedback'
import { api } from '@/lib/api'
import { useWizard } from '@/features/vm-provisioning/context'
import {
  useApplications,
  useCertificatePackages,
  useClusters,
  useDatacenters,
  useDatastores,
  useHosts,
  useNetworks,
  useSites,
  useTemplates,
  useVcenters,
} from '@/features/vm-provisioning/hooks'
import type { PreflightReport } from '@/types/api'

function Section({ title, rows }: { title: string; rows: Array<[string, ReactNode]> }) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</p>
      <dl className="space-y-1 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex gap-2">
            <dt className="w-36 shrink-0 text-slate-500">{label}</dt>
            <dd className="min-w-0 break-words font-medium text-slate-800">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export function ReviewStep() {
  const wizard = useWizard()
  const data = wizard.data
  const payload = wizard.requestPayload()

  const vcenters = useVcenters()
  const sites = useSites()
  const datacenters = useDatacenters(data.vcenter_id)
  const clusters = useClusters(data.vcenter_id, data.datacenter_id)
  const hosts = useHosts(data.vcenter_id, data.cluster_id)
  const datastores = useDatastores(data.vcenter_id, data.cluster_id)
  const networks = useNetworks(data.vcenter_id, data.datacenter_id)
  const templates = useTemplates(data.vcenter_id, data.datacenter_id, data.source_type === 'template')
  const packages = useCertificatePackages()
  const applications = useApplications()

  const [report, setReport] = useState<PreflightReport | null>(null)
  const dryRun = useMutation({
    mutationFn: () => api.validate(payload),
    onSuccess: (result) => setReport(result),
  })

  const nameOf = (list: Array<{ id: string; name: string }> | undefined, id: string) =>
    list?.find((entry) => entry.id === id)?.name ?? (id || '—')
  const selectedDisksTotal = data.disks.reduce((total, disk) => total + disk.size_gb, 0)
  const dnsServers = [data.dns_primary, data.dns_secondary, ...data.dns_extra.split(/[\s,;]+/)]
    .map((entry) => entry.trim())
    .filter(Boolean)
    .join(', ')

  const sourceRows: Array<[string, ReactNode]> = [
    ['Creation method', data.source_type === 'template' ? 'From template' : 'Blank virtual machine'],
  ]
  if (data.source_type === 'template') {
    sourceRows.push(['Template', nameOf(templates.data, data.template_id)])
  }

  const osRows: Array<[string, ReactNode]> = data.source_type === 'blank'
    ? [['Installation', 'Not installed; VM remains powered off']]
    : [
        ['Derived from', nameOf(templates.data, data.template_id)],
        ['Computer name', data.hostname || data.vm_name],
      ]
  if (data.source_type === 'template' && data.timezone) osRows.push(['Time zone', data.timezone])
  if (data.source_type === 'template' && data.domain_join.enabled) {
    osRows.push(['Domain join', data.domain_join.domain], ['OU', data.domain_join.ou || '(default)'])
  }

  return (
    <section aria-label="Review and provision" className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Review and confirm</h2>
          <p className="mt-1 text-xs text-slate-500">
            Verify the target and configuration, then run a non-destructive preflight check.
          </p>
        </div>
        <Button type="button" variant="secondary" loading={dryRun.isPending} onClick={() => dryRun.mutate()}>
          Dry Run / Validate
        </Button>
      </header>

      {dryRun.isPending && (
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <Spinner className="h-3.5 w-3.5" /> Running preflight validation…
        </div>
      )}
      {dryRun.isError && (
        <Alert tone="danger" title="Dry run failed">
          {dryRun.error instanceof Error ? dryRun.error.message : 'The validation request could not be completed.'}
        </Alert>
      )}
      {report && (
        <div className="rounded-md border border-slate-200">
          <div className="border-b border-slate-100 px-3 py-2">
            <Alert tone={report.ready ? 'success' : 'danger'}>{report.summary}</Alert>
          </div>
          <ul className="divide-y divide-slate-100">
            {report.checks.map((check) => (
              <li key={check.code} className="flex items-start gap-2 px-3 py-1.5 text-sm">
                <CheckStatusBadge status={check.status} />
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-slate-700">{check.label}</span>
                  {check.detail && <span className="block whitespace-pre-wrap text-xs text-slate-500">{check.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section title="Source" rows={sourceRows} />
        <Section
          title="Infrastructure"
          rows={[
            ['vCenter', nameOf(vcenters.data, data.vcenter_id)],
            ['Site', nameOf(sites.data, data.site_id)],
            ['Datacenter', nameOf(datacenters.data, data.datacenter_id)],
            ['Cluster', nameOf(clusters.data, data.cluster_id)],
            ['Host', data.host_mode === 'auto' ? 'Automatic placement' : nameOf(hosts.data, data.host_id ?? '')],
          ]}
        />
        <Section
          title="VM and compute"
          rows={[
            ['Name', data.vm_name || '—'],
            ['Description', data.description || '—'],
            ['CPU', `${data.cpu} vCPU`],
            ['Memory', `${data.memory_gb} GB`],
            ['Firmware', data.firmware === 'EFI' ? `UEFI${data.secure_boot ? ' + Secure Boot' : ''}` : 'BIOS'],
          ]}
        />
        <Section
          title="Storage"
          rows={[
            ['Datastore', data.storage_mode === 'auto' ? 'Automatic' : nameOf(datastores.data, data.datastore_id ?? '')],
            ...data.disks.map((disk, index) => [
              `Disk ${index + 1}`,
              `${disk.size_gb} GB (${disk.provisioning})`,
            ] as [string, ReactNode]),
            ['Total', `${selectedDisksTotal} GB`],
          ]}
        />
        <Section
          title="Network"
          rows={[
            ['Port group', nameOf(networks.data, data.network_id)],
            ['Adapter', data.adapter_type],
            ['IP mode', data.source_type === 'blank' ? 'Configure after OS installation' : data.ip_mode],
            ...(data.source_type === 'template' && data.ip_mode === 'STATIC'
              ? ([
                  ['IP address', `${data.ip_address}/${data.prefix_input}`],
                  ['Gateway', data.gateway],
                  ['DNS servers', dnsServers || '—'],
                ] as Array<[string, ReactNode]>)
              : []),
          ]}
        />
        <Section title="Operating system" rows={osRows} />
        <Section
          title="Certificates"
          rows={[[
            'Packages',
            data.source_type === 'blank' || data.certificate_package_ids.length === 0
              ? 'None'
              : data.certificate_package_ids.map((id) => nameOf(packages.data, id)).join(', '),
          ]]}
        />
        <Section
          title="Applications"
          rows={[[
            'Selected',
            data.source_type === 'blank' || data.application_ids.length === 0
              ? 'None'
              : data.application_ids.map((id) => nameOf(applications.data, id)).join(', '),
          ]]}
        />
      </div>

      <Alert tone="warning" title="This action creates real infrastructure">
        Submission performs preflight validation again and requires an explicit click. You will then be redirected
        to the asynchronous job progress view.
      </Alert>
    </section>
  )
}
