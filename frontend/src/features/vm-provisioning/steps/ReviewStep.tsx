import { useMutation } from '@tanstack/react-query'
import { CheckCircle2, ClipboardCheck, PlayCircle, TriangleAlert } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, CheckStatusBadge, Spinner } from '@/components/ui/feedback'
import { useWizard } from '@/features/vm-provisioning/context'
import {
  useApplications,
  useCertificatePackages,
  useClusters,
  useDatacenters,
  useDatastores,
  useHosts,
  useNetworks,
  useTemplates,
  useVcenters,
} from '@/features/vm-provisioning/hooks'
import { api } from '@/lib/api'
import type { PreflightReport } from '@/types/api'

function PlanSection({ title, rows }: { title: string; rows: Array<[string, ReactNode]> }) {
  return (
    <div className="console-group">
      <div className="border-b border-slate-200 bg-slate-50 px-3 py-2.5">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-700">{title}</p>
      </div>
      <dl className="divide-y divide-slate-100 px-3">
        {rows.map(([label, value]) => (
          <div key={label} className="grid grid-cols-[120px_minmax(0,1fr)] gap-3 py-2 text-xs">
            <dt className="text-slate-500">{label}</dt>
            <dd className="min-w-0 break-words font-medium text-slate-900">{value}</dd>
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

  const nameOf = (list: Array<{ id: string; name: string }> | undefined, id: string) => {
    if (!id) return '—'
    if (!list) return 'Resolving inventory…'
    return list.find((entry) => entry.id === id)?.name ?? 'Selection unavailable'
  }
  const selectedDisksTotal = data.disks.reduce((total, disk) => total + disk.size_gb, 0)
  const dnsServers = [data.dns_primary, data.dns_secondary, ...data.dns_extra.split(/[\s,;]+/)]
    .map((entry) => entry.trim())
    .filter(Boolean)
    .join(', ')

  const sourceRows: Array<[string, ReactNode]> = [
    ['Creation method', data.source_type === 'template' ? 'Deploy from template' : 'Blank virtual machine'],
  ]
  if (data.source_type === 'template') sourceRows.push(['Template', nameOf(templates.data, data.template_id)])

  const osRows: Array<[string, ReactNode]> = data.source_type === 'blank'
    ? [['Installation state', 'No OS · VM remains powered off']]
    : [
        ['Operating system', templates.data?.find((entry) => entry.id === data.template_id)?.os_version || 'Derived from template'],
        ['Computer name', data.hostname || data.vm_name],
      ]
  if (data.source_type === 'template' && data.timezone) osRows.push(['Time zone', data.timezone])
  if (data.source_type === 'template' && data.domain_join.enabled) {
    osRows.push(['Domain join', data.domain_join.domain], ['OU', data.domain_join.ou || 'Domain default'])
  }

  return (
    <section aria-label="Review and provision" className="space-y-5">
      <header>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Change review</p>
            <h2>Validate the deployment plan</h2>
            <p>Confirm every target and override before creating infrastructure.</p>
          </div>
          <Button type="button" variant="secondary" loading={dryRun.isPending} onClick={() => dryRun.mutate()}>
            <PlayCircle className="h-4 w-4" /> Run preflight
          </Button>
        </div>
      </header>

      {dryRun.isPending && (
        <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
          <Spinner className="h-3.5 w-3.5" /> Querying inventory, capacity, policy, and credential providers…
        </div>
      )}
      {dryRun.isError && (
        <Alert tone="danger" title="Preflight request failed">
          {dryRun.error instanceof Error ? dryRun.error.message : 'Validation could not be completed.'}
        </Alert>
      )}

      {report && (
        <div className="console-group">
          <div className={`flex items-center justify-between gap-3 border-b px-4 py-3 ${report.ready ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'}`}>
            <div className="flex items-start gap-2">
              {report.ready
                ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-700" />
                : <TriangleAlert className="mt-0.5 h-4 w-4 text-red-700" />}
              <div>
                <p className={`text-sm font-semibold ${report.ready ? 'text-emerald-900' : 'text-red-900'}`}>
                  {report.ready ? 'Deployment is ready' : 'Deployment is blocked'}
                </p>
                <p className={`text-xs ${report.ready ? 'text-emerald-800' : 'text-red-800'}`}>{report.summary}</p>
              </div>
            </div>
            <Badge tone={report.ready ? 'success' : 'danger'}>{report.checks.length} checks</Badge>
          </div>
          <ul className="max-h-72 divide-y divide-slate-100 overflow-y-auto">
            {report.checks.map((check) => (
              <li key={check.code} className="grid grid-cols-[80px_minmax(0,1fr)] items-start gap-3 px-4 py-2.5 text-xs">
                <CheckStatusBadge status={check.status} />
                <span className="min-w-0">
                  <span className="font-semibold text-slate-800">{check.label}</span>
                  {check.detail && <span className="mt-0.5 block whitespace-pre-wrap text-slate-500">{check.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <PlanSection title="Source" rows={sourceRows} />
        <PlanSection
          title="Infrastructure"
          rows={[
            ['vCenter', nameOf(vcenters.data, data.vcenter_id)],
            ['Datacenter', nameOf(datacenters.data, data.datacenter_id)],
            ['Cluster', nameOf(clusters.data, data.cluster_id)],
            ['Host', data.host_mode === 'auto' ? 'Automatic placement' : nameOf(hosts.data, data.host_id ?? '')],
          ]}
        />
        <PlanSection
          title="Virtual machine"
          rows={[
            ['Name', data.vm_name || '—'],
            ['Description', data.description || '—'],
            ['Compute', `${data.cpu} vCPU · ${data.memory_gb} GB RAM`],
            ['Firmware', data.firmware === 'EFI' ? `UEFI${data.secure_boot ? ' · Secure Boot' : ''}` : 'BIOS'],
          ]}
        />
        <PlanSection
          title="Storage"
          rows={[
            ['Datastore', data.storage_mode === 'auto' ? 'Automatic placement' : nameOf(datastores.data, data.datastore_id ?? '')],
            ...data.disks.map((disk, index) => [`Disk ${index + 1}`, `${disk.size_gb} GB · ${disk.provisioning}`] as [string, ReactNode]),
            ['Total capacity', `${selectedDisksTotal} GB`],
          ]}
        />
        <PlanSection
          title="Network"
          rows={[
            ['Port group', nameOf(networks.data, data.network_id)],
            ['Adapter', data.adapter_type],
            ['Addressing', data.source_type === 'blank' ? 'After OS installation' : data.ip_mode],
            ...(data.source_type === 'template' && data.ip_mode === 'STATIC'
              ? ([
                  ['IP address', `${data.ip_address}/${data.prefix_input}`],
                  ['Gateway', data.gateway],
                  ['DNS servers', dnsServers || '—'],
                ] as Array<[string, ReactNode]>)
              : []),
          ]}
        />
        <PlanSection title="Operating system" rows={osRows} />
        <PlanSection
          title="Security"
          rows={[[
            'Certificates',
            data.source_type === 'blank'
              ? 'Not applicable to blank VM'
              : data.certificate_package_ids.length === 0
                ? 'None selected'
                : data.certificate_package_ids.map((id) => nameOf(packages.data, id)).join(', '),
          ]]}
        />
        <PlanSection
          title="Applications"
          rows={[[
            'Packages',
            data.source_type === 'blank'
              ? 'Not applicable to blank VM'
              : data.application_ids.length === 0
                ? 'None selected'
                : data.application_ids.map((id) => nameOf(applications.data, id)).join(', '),
          ]]}
        />
      </div>

      <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-900">
        <ClipboardCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          Submission runs preflight again and creates an audited asynchronous job. Nothing is provisioned until
          you use the confirmation button below.
        </span>
      </div>
    </section>
  )
}
