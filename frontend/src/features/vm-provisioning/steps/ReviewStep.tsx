import { useMutation } from '@tanstack/react-query'
import {
  Boxes,
  CheckCircle2,
  ClipboardCheck,
  Cpu,
  Disc3,
  HardDrive,
  Network,
  PlayCircle,
  RefreshCw,
  Server,
  TriangleAlert,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, CheckStatusBadge, LoadingState } from '@/components/ui/feedback'
import { useWizard } from '@/features/vm-provisioning/context'
import {
  useApplications,
  useCertificatePackages,
  useClusters,
  useDatacenters,
  useDatastores,
  useHosts,
  useIsos,
  useNetworks,
  useResourcePools,
  useTemplates,
  useVcenters,
} from '@/features/vm-provisioning/hooks'
import { api } from '@/lib/api'
import { displayValue } from '@/lib/utils'
import type { PreflightReport } from '@/types/api'

interface NameLookup {
  data?: Array<{ id: string; name: string }>
  isLoading: boolean
  isError: boolean
}

function nameOf(lookup: NameLookup, id: string | null): string {
  if (!id) return 'Not selected'
  const match = lookup.data?.find((entry) => entry.id === id)
  if (match) return match.name
  if (lookup.isLoading) return 'Loading selection details…'
  if (lookup.isError) return 'Selection details unavailable'
  return 'No longer available in this target'
}

function SummarySection({
  icon: Icon,
  title,
  rows,
}: {
  icon: typeof Server
  title: string
  rows: Array<[string, ReactNode]>
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white">
      <header className="flex items-center gap-3 border-b border-[#e3e7e2] bg-[#f8f9f6] px-5 py-4">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-50 text-brand-700">
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <h3 className="text-sm font-semibold text-[#202923]">{title}</h3>
      </header>
      <dl className="divide-y divide-[#ecefeb]">
        {rows.map(([label, value]) => (
          <div key={label} className="grid grid-cols-[minmax(110px,0.36fr)_minmax(0,1fr)] gap-4 px-5 py-3 text-xs">
            <dt className="text-[#758079]">{label}</dt>
            <dd className="min-w-0 break-words font-medium text-[#27302c]">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
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
  const resourcePools = useResourcePools(data.vcenter_id, data.cluster_id)
  const datastores = useDatastores(data.vcenter_id, data.cluster_id)
  const networks = useNetworks(data.vcenter_id, data.datacenter_id)
  const templates = useTemplates(data.vcenter_id, data.datacenter_id, data.source_type === 'template')
  const isos = useIsos(data.vcenter_id, data.datacenter_id, data.source_type === 'blank')
  const packages = useCertificatePackages(data.source_type === 'template' && data.certificate_package_ids.length > 0)
  const applications = useApplications(data.source_type === 'template' && data.application_ids.length > 0)
  const [report, setReport] = useState<PreflightReport | null>(null)

  const dryRun = useMutation({
    mutationFn: () => api.validate(payload),
    onSuccess: setReport,
  })

  const selectedTemplate = templates.data?.find((item) => item.id === data.template_id)
  const selectedIso = isos.data?.find((item) => item.id === data.iso_id)
  const diskTotal = data.disks.reduce((total, disk) => total + disk.size_gb, 0)
  const dns = [data.dns_primary, data.dns_secondary, ...data.dns_extra.split(/[\s,;]+/)].filter(Boolean)

  useEffect(() => {
    if (vcenters.isSuccess && data.vcenter_id && !vcenters.data.some((item) => item.id === data.vcenter_id)) {
      wizard.update({ vcenter_id: '' })
      return
    }
    if (datacenters.isSuccess && data.datacenter_id && !datacenters.data.some((item) => item.id === data.datacenter_id)) {
      wizard.update({ datacenter_id: '' })
      return
    }
    if (clusters.isSuccess && data.cluster_id && !clusters.data.some((item) => item.id === data.cluster_id)) {
      wizard.update({ cluster_id: '' })
      return
    }
    if (hosts.isSuccess && data.host_id && !hosts.data.some((item) => item.id === data.host_id)) {
      wizard.update({ host_mode: 'auto', host_id: null })
      return
    }
    if (
      resourcePools.isSuccess
      && data.resource_pool_id
      && !resourcePools.data.some((item) => item.id === data.resource_pool_id)
    ) {
      wizard.update({ resource_pool_id: null })
      return
    }
    if (networks.isSuccess && data.network_id && !networks.data.some((item) => item.id === data.network_id)) {
      wizard.update({ network_id: '' })
      return
    }
    if (
      templates.isSuccess
      && data.template_id
      && !templates.data.some((item) => item.id === data.template_id)
    ) {
      wizard.update({ template_id: '' })
      return
    }
    if (isos.isSuccess && data.iso_id && !isos.data.some((item) => item.id === data.iso_id)) {
      wizard.update({ iso_id: null })
      return
    }
    if (datastores.isSuccess) {
      const available = new Set(datastores.data.filter((item) => item.accessible).map((item) => item.id))
      if (available.size === 0 && data.storage_mode === 'auto') {
        wizard.update({ storage_mode: 'manual', datastore_id: null })
        return
      }
      const nextDatastoreId = data.datastore_id && available.has(data.datastore_id) ? data.datastore_id : null
      const nextDisks = data.disks.map((disk) => ({
        ...disk,
        datastore_id: disk.datastore_id && available.has(disk.datastore_id) ? disk.datastore_id : null,
      }))
      const disksChanged = nextDisks.some((disk, index) => disk.datastore_id !== data.disks[index].datastore_id)
      if (nextDatastoreId !== data.datastore_id || disksChanged) {
        wizard.update({ datastore_id: nextDatastoreId, disks: nextDisks })
        return
      }
    }
    if (packages.isSuccess && data.certificate_package_ids.length > 0) {
      const available = new Set(packages.data.map((item) => item.id))
      const selected = data.certificate_package_ids.filter((id) => available.has(id))
      if (selected.length !== data.certificate_package_ids.length) {
        wizard.update({ certificate_package_ids: selected })
        return
      }
    }
    if (applications.isSuccess && data.application_ids.length > 0) {
      const available = new Set(applications.data.map((item) => item.id))
      const selected = data.application_ids.filter((id) => available.has(id))
      if (selected.length !== data.application_ids.length) wizard.update({ application_ids: selected })
    }
  }, [
    applications.data,
    applications.isSuccess,
    clusters.data,
    clusters.isSuccess,
    data,
    datacenters.data,
    datacenters.isSuccess,
    datastores.data,
    datastores.isSuccess,
    hosts.data,
    hosts.isSuccess,
    isos.data,
    isos.isSuccess,
    networks.data,
    networks.isSuccess,
    packages.data,
    packages.isSuccess,
    resourcePools.data,
    resourcePools.isSuccess,
    templates.data,
    templates.isSuccess,
    vcenters.data,
    vcenters.isSuccess,
    wizard.update,
  ])

  const lookupStates: Array<{ isLoading: boolean; isError: boolean; refetch: () => Promise<unknown> }> = [
    vcenters,
    datacenters,
    clusters,
    hosts,
    resourcePools,
    datastores,
    networks,
    ...(data.source_type === 'template' ? [templates] : [isos]),
    ...(data.certificate_package_ids.length > 0 ? [packages] : []),
    ...(data.application_ids.length > 0 ? [applications] : []),
  ]
  const selectionDetailsLoading = lookupStates.some((query) => query.isLoading)
  const selectionDetailsError = lookupStates.some((query) => query.isError)

  function retrySelectionDetails() {
    lookupStates.filter((query) => query.isError).forEach((query) => void query.refetch())
  }

  return (
    <section aria-label="Review deployment" className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="console-kicker">Step 6 · Review</p>
          <h2>Review the deployment plan</h2>
          <p>Confirm the human-readable selections below. InfraOps validates the live inventory again before it creates anything.</p>
        </div>
        <Button
          type="button"
          variant="secondary"
          loading={dryRun.isPending}
          disabled={selectionDetailsLoading || selectionDetailsError}
          onClick={() => dryRun.mutate()}
        >
          <PlayCircle className="h-4 w-4" /> Run preflight checks
        </Button>
      </header>

      {dryRun.isPending && <LoadingState title="Checking deployment readiness" description="Validating inventory, capacity, networking, and platform policy." />}
      {selectionDetailsLoading && (
        <LoadingState title="Loading selection details" description="Resolving current names for the chosen infrastructure, source, storage, and network." />
      )}
      {selectionDetailsError && (
        <Alert tone="danger" title="Some selection details could not be loaded">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>Retry the inventory lookups before running preflight checks.</span>
            <Button type="button" size="sm" variant="secondary" onClick={retrySelectionDetails}>
              <RefreshCw className="h-3.5 w-3.5" /> Try again
            </Button>
          </div>
        </Alert>
      )}
      {dryRun.isError && (
        <Alert tone="danger" title="Preflight checks could not be completed">
          {dryRun.error instanceof Error ? dryRun.error.message : 'Try the validation again.'}
        </Alert>
      )}

      {report && (
        <div className="overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white">
          <div className={`flex items-start justify-between gap-4 border-b px-5 py-4 ${report.ready ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'}`}>
            <div className="flex items-start gap-3">
              {report.ready
                ? <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-700" />
                : <TriangleAlert className="mt-0.5 h-5 w-5 text-red-700" />}
              <div>
                <p className={`text-sm font-semibold ${report.ready ? 'text-emerald-950' : 'text-red-950'}`}>
                  {report.ready ? 'Ready to deploy' : 'Changes are required'}
                </p>
                <p className={`mt-1 text-xs ${report.ready ? 'text-emerald-800' : 'text-red-800'}`}>{report.summary}</p>
              </div>
            </div>
            <Badge tone={report.ready ? 'success' : 'danger'}>{report.checks.length} checks</Badge>
          </div>
          <ul className="max-h-80 divide-y divide-[#ecefeb] overflow-y-auto">
            {report.checks.map((check) => (
              <li key={check.code} className="grid gap-3 px-5 py-3 text-xs sm:grid-cols-[92px_minmax(0,1fr)]">
                <CheckStatusBadge status={check.status} />
                <span>
                  <span className="font-semibold text-[#27302c]">{check.label}</span>
                  {check.detail && <span className="mt-0.5 block text-[#68736d]">{check.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <SummarySection
          icon={Boxes}
          title="Deployment source"
          rows={data.source_type === 'template'
            ? [
                ['Method', 'OVF / OVA deployment'],
                ['Package', selectedTemplate?.name ?? nameOf(templates, data.template_id)],
                ['Package type', selectedTemplate?.type ?? 'OVF / OVA'],
                ['Storage', selectedTemplate?.storage_name ?? 'Storage details not available'],
              ]
            : [
                ['Method', 'Blank virtual machine'],
                ['Installation media', selectedIso?.name ?? (data.iso_id ? nameOf(isos, data.iso_id) : 'No ISO')],
                ['Media storage', selectedIso?.datastore_name ?? (data.iso_id ? 'Storage details not available' : 'Not applicable')],
              ]}
        />

        <SummarySection
          icon={Server}
          title="Location"
          rows={[
            ['vCenter', nameOf(vcenters, data.vcenter_id)],
            ['Datacenter', nameOf(datacenters, data.datacenter_id)],
            ['Compute target', nameOf(clusters, data.cluster_id)],
            ['Host', data.host_mode === 'auto' ? 'Automatic placement' : nameOf(hosts, data.host_id)],
            ['Resource pool', data.resource_pool_id ? nameOf(resourcePools, data.resource_pool_id) : 'Compute target default'],
          ]}
        />

        <SummarySection
          icon={Cpu}
          title="Compute"
          rows={[
            ['VM name', displayValue(data.vm_name)],
            ['Description', displayValue(data.description)],
            ['Resources', `${data.cpu} vCPU · ${data.memory_gb} GB memory`],
            ['Boot mode', data.firmware === 'EFI' ? `UEFI${data.secure_boot ? ' with Secure Boot' : ''}` : 'Legacy BIOS'],
          ]}
        />

        <SummarySection
          icon={HardDrive}
          title="Storage"
          rows={[
            ['Placement', data.storage_mode === 'auto' ? 'Automatic capacity-aware placement' : nameOf(datastores, data.datastore_id)],
            ...data.disks.map((disk, index) => [`Disk ${index + 1}`, `${disk.size_gb} GB · ${disk.provisioning === 'thin' ? 'Thin provisioned' : 'Thick provisioned'}`] as [string, ReactNode]),
            ['Total requested', `${diskTotal} GB`],
          ]}
        />

        <SummarySection
          icon={Network}
          title="Network"
          rows={[
            ['Network', nameOf(networks, data.network_id)],
            ['Adapter', data.adapter_type === 'VMXNET3' ? 'VMXNET 3 (recommended)' : 'Intel E1000E'],
            ['Addressing', data.source_type === 'blank' ? 'Configured during OS installation' : data.ip_mode === 'DHCP' ? 'DHCP' : 'Static IPv4'],
            ...(data.source_type === 'template' && data.ip_mode === 'STATIC'
              ? [
                  ['IP address', `${data.ip_address}/${data.prefix_input}`],
                  ['Gateway', data.gateway],
                  ['DNS servers', dns.join(', ') || 'Not configured'],
                ] as Array<[string, ReactNode]>
              : []),
          ]}
        />

        <SummarySection
          icon={Disc3}
          title="Guest automation"
          rows={data.source_type === 'blank'
            ? [['Post-deployment state', data.iso_id ? 'Powered off with the selected ISO mounted' : 'Powered off with no ISO mounted']]
            : [
                ['Computer name', data.hostname || data.vm_name],
                ['Domain membership', data.domain_join.enabled ? data.domain_join.domain : 'No domain join'],
                ['Certificate packages', data.certificate_package_ids.length ? data.certificate_package_ids.map((id) => nameOf(packages, id)).join(', ') : 'None selected'],
                ['Applications', data.application_ids.length ? data.application_ids.map((id) => nameOf(applications, id)).join(', ') : 'None selected'],
              ]}
        />
      </div>

      <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-950">
        <ClipboardCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <span>Creating this VM starts an audited deployment job. The request is validated once more against the current datacenter inventory before any infrastructure is changed.</span>
      </div>
    </section>
  )
}
