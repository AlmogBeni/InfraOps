import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  ChevronDown,
  Clock3,
  Cpu,
  HardDrive,
  MonitorCog,
  Network,
  PackageCheck,
  RotateCcw,
  Server,
  Terminal,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import {
  Alert,
  Badge,
  CheckStatusBadge,
  EmptyState,
  JobStatusBadge,
  LoadingState,
  ProgressBar,
  StepStatusIcon,
} from '@/components/ui/feedback'
import { LogViewer } from '@/components/ui/log-viewer'
import { ConsolePanel, DataPoint, PageHeader, PanelHeader } from '@/components/ui/page'
import { useJobEvents } from '@/features/jobs/useJobEvents'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import {
  cn,
  formatDateTime,
  formatDuration,
  formatTime,
  humanizeIdentifier,
  humanizeStageKey,
} from '@/lib/utils'
import type {
  CheckStatus,
  JobDetailOut,
  JobStepOut,
  ProvisioningRequest,
  StepStatus,
} from '@/types/api'

interface ChecklistItem {
  group: string
  label: string
  status: CheckStatus
  detail?: string
}

interface NamedInventoryItem {
  id: string
  name: string
}

const STEP_TONES: Record<StepStatus, Parameters<typeof Badge>[0]['tone']> = {
  PENDING: 'neutral',
  RUNNING: 'running',
  SUCCEEDED: 'success',
  FAILED: 'danger',
  SKIPPED: 'neutral',
  CANCELLED: 'warning',
}

function stageTiming(step: JobStepOut): string {
  if (!step.started_at) return 'Waiting to start'
  const start = formatTime(step.started_at)
  if (!step.finished_at) return `${start} → running now`
  const durationSeconds = Math.max(
    0,
    (new Date(step.finished_at).getTime() - new Date(step.started_at).getTime()) / 1000,
  )
  return `${start} → ${formatTime(step.finished_at)} · ${formatDuration(durationSeconds)}`
}

function mutationMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : 'The deployment service could not complete the action. Try again.'
}

function resolveName(
  items: NamedInventoryItem[] | undefined,
  id: string | null | undefined,
  isLoading: boolean,
  emptyLabel: string,
): string {
  if (!id) return emptyLabel
  const match = items?.find((item) => item.id === id)
  if (match) return match.name
  return isLoading ? 'Resolving name…' : 'Unavailable in current inventory'
}

function useRequestLabels(request: ProvisioningRequest, persistedDatacenterName?: string | null) {
  const { vcenter_id: vcenterId, datacenter_id: datacenterId, cluster_id: clusterId } = request.compute
  const hasDatastoreSelection = request.hardware.disks.some((disk) => Boolean(disk.datastore_id))

  const vcenters = useQuery({
    queryKey: ['vcenters'],
    queryFn: () => api.vcenters(),
    staleTime: 5 * 60_000,
  })
  const datacenters = useQuery({
    queryKey: ['datacenters', vcenterId],
    queryFn: () => api.datacenters(vcenterId),
    enabled: Boolean(vcenterId),
    staleTime: 5 * 60_000,
  })
  const clusters = useQuery({
    queryKey: ['clusters', vcenterId, datacenterId],
    queryFn: () => api.clusters(vcenterId, datacenterId),
    enabled: Boolean(vcenterId && datacenterId),
    staleTime: 5 * 60_000,
  })
  const hosts = useQuery({
    queryKey: ['hosts', vcenterId, clusterId],
    queryFn: () => api.hosts(vcenterId, clusterId),
    enabled: Boolean(vcenterId && clusterId && request.compute.host_id),
    staleTime: 5 * 60_000,
  })
  const resourcePools = useQuery({
    queryKey: ['resource-pools', vcenterId, clusterId],
    queryFn: () => api.resourcePools(vcenterId, clusterId),
    enabled: Boolean(vcenterId && clusterId && request.compute.resource_pool_id),
    staleTime: 5 * 60_000,
  })
  const datastores = useQuery({
    queryKey: ['datastores', vcenterId, clusterId],
    queryFn: () => api.datastores(vcenterId, clusterId),
    enabled: Boolean(vcenterId && clusterId && hasDatastoreSelection),
    staleTime: 5 * 60_000,
  })
  const networks = useQuery({
    queryKey: ['networks', vcenterId, datacenterId],
    queryFn: () => api.networks(vcenterId, datacenterId),
    enabled: Boolean(vcenterId && datacenterId),
    staleTime: 5 * 60_000,
  })
  const templates = useQuery({
    queryKey: ['templates', vcenterId, datacenterId],
    queryFn: () => api.templates(vcenterId, datacenterId),
    enabled: Boolean(vcenterId && datacenterId && request.guest.template_id),
    staleTime: 5 * 60_000,
  })
  const isos = useQuery({
    queryKey: ['isos', vcenterId, datacenterId],
    queryFn: () => api.isos(vcenterId, datacenterId),
    enabled: Boolean(vcenterId && datacenterId && request.guest.iso_id),
    staleTime: 5 * 60_000,
  })
  const applications = useQuery({
    queryKey: ['applications', 'all'],
    queryFn: () => api.applications(false),
    enabled: request.application_ids.length > 0,
    staleTime: 5 * 60_000,
  })
  const certificatePackages = useQuery({
    queryKey: ['certificate-packages', 'all'],
    queryFn: () => api.certificatePackages(false),
    enabled: request.certificate_package_ids.length > 0,
    staleTime: 5 * 60_000,
  })

  const relevantQueries = [
    vcenters,
    datacenters,
    clusters,
    networks,
    ...(request.compute.host_id ? [hosts] : []),
    ...(request.compute.resource_pool_id ? [resourcePools] : []),
    ...(hasDatastoreSelection ? [datastores] : []),
    ...(request.guest.template_id ? [templates] : []),
    ...(request.guest.iso_id ? [isos] : []),
    ...(request.application_ids.length ? [applications] : []),
    ...(request.certificate_package_ids.length ? [certificatePackages] : []),
  ]

  return {
    vcenter: resolveName(vcenters.data, vcenterId, vcenters.isLoading, 'No vCenter selected'),
    datacenter: persistedDatacenterName
      ?? resolveName(datacenters.data, datacenterId, datacenters.isLoading, 'No datacenter selected'),
    cluster: resolveName(clusters.data, clusterId, clusters.isLoading, 'No compute target selected'),
    host: resolveName(hosts.data, request.compute.host_id, hosts.isLoading, 'Automatic host selection'),
    resourcePool: resolveName(
      resourcePools.data,
      request.compute.resource_pool_id,
      resourcePools.isLoading,
      'Default resource pool',
    ),
    network: resolveName(networks.data, request.network.network_id, networks.isLoading, 'No network selected'),
    template: resolveName(templates.data, request.guest.template_id, templates.isLoading, 'No template selected'),
    iso: resolveName(isos.data, request.guest.iso_id, isos.isLoading, 'No installation media'),
    datastore: (id: string | null) => resolveName(datastores.data, id, datastores.isLoading, 'Automatic placement'),
    applicationNames: request.application_ids.map((id) =>
      resolveName(applications.data, id, applications.isLoading, 'No applications selected'),
    ),
    certificateNames: request.certificate_package_ids.map((id) =>
      resolveName(certificatePackages.data, id, certificatePackages.isLoading, 'No certificate packages selected'),
    ),
    isResolving: relevantQueries.some((query) => query.isLoading),
    hasLookupError: relevantQueries.some((query) => query.isError),
  }
}

function DetailLine({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex min-w-0 items-start justify-between gap-4 border-t border-[#e7eae6] py-2 first:border-t-0 first:pt-0 last:pb-0">
      <dt className="shrink-0 text-[11px] text-[#758079]">{label}</dt>
      <dd className={cn('min-w-0 text-right text-xs font-medium leading-5 text-[#303a34]', mono && 'font-mono tabular-nums')}>
        {value}
      </dd>
    </div>
  )
}

function SnapshotSection({
  icon: Icon,
  title,
  summary,
  children,
  className,
}: {
  icon: LucideIcon
  title: string
  summary: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={cn('rounded-xl border border-[#dfe4de] bg-[#fafbf8] p-4', className)}>
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-[#d8ddd7] bg-white text-brand-700">
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <h4 className="text-xs font-semibold text-[#202923]">{title}</h4>
          <p className="mt-0.5 truncate text-[11px] text-[#68736d]" title={summary}>{summary}</p>
        </div>
      </div>
      <dl className="mt-4">{children}</dl>
    </section>
  )
}

function RequestSnapshot({
  request,
  datacenterName,
}: {
  request: ProvisioningRequest
  datacenterName?: string | null
}) {
  const labels = useRequestLabels(request, datacenterName)
  const memoryGb = request.hardware.memory_mb / 1024
  const memoryLabel = `${Number.isInteger(memoryGb) ? memoryGb : memoryGb.toFixed(1)} GB`
  const storageGb = request.hardware.disks.reduce((total, disk) => total + disk.size_gb, 0)
  const sourceLabel = request.guest.template_id
    ? labels.template
    : request.guest.iso_id
      ? labels.iso
      : 'No ISO mounted'
  const networkAddress = request.network.mode === 'STATIC' && request.network.ipv4
    ? `${request.network.ipv4.address}/${request.network.ipv4.prefix}`
    : 'Assigned automatically by DHCP'

  return (
    <ConsolePanel>
      <PanelHeader
        title="Submitted configuration"
        description="Readable placement, sizing, operating system, and configuration choices captured when this deployment was queued."
        actions={labels.isResolving
          ? <Badge tone="info"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-600" /> Resolving names</Badge>
          : undefined}
      />
      {labels.hasLookupError && (
        <div className="border-b border-[#e2e6e1] px-5 py-3">
          <Alert tone="warning" title="Some current inventory names are unavailable">
            Historical configuration is preserved below; an item may have been removed or the inventory service may be temporarily unavailable.
          </Alert>
        </div>
      )}
      <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-3">
        <SnapshotSection icon={Server} title="Location" summary={`${labels.datacenter} · ${labels.vcenter}`}>
          <DetailLine label="vCenter" value={labels.vcenter} />
          <DetailLine label="Datacenter" value={labels.datacenter} />
          <DetailLine label="Compute target" value={labels.cluster} />
          <DetailLine label="Host placement" value={labels.host} />
          <DetailLine label="Resource pool" value={labels.resourcePool} />
        </SnapshotSection>

        <SnapshotSection icon={MonitorCog} title="Source and guest" summary={sourceLabel}>
          <DetailLine label="Source" value={request.source_type === 'template' ? 'OVF / OVA deployment' : 'Blank virtual machine'} />
          <DetailLine label={request.guest.template_id ? 'Template' : 'Installation media'} value={sourceLabel} />
          <DetailLine label="Guest hostname" value={request.guest.hostname ?? (request.source_type === 'blank' ? 'Configured during OS installation' : 'Uses the VM name')} />
          <DetailLine label="Time zone" value={request.guest.timezone ?? (request.source_type === 'blank' ? 'Configured during OS installation' : 'Platform default')} />
        </SnapshotSection>

        <SnapshotSection icon={Cpu} title="Compute and firmware" summary={`${request.hardware.cpu} vCPU · ${memoryLabel}`}>
          <DetailLine label="Processors" value={`${request.hardware.cpu} vCPU`} />
          <DetailLine label="Memory" value={memoryLabel} />
          <DetailLine label="Firmware" value={request.hardware.firmware === 'EFI' ? 'UEFI' : 'Legacy BIOS'} />
          <DetailLine label="Secure Boot" value={request.hardware.secure_boot ? 'Enabled' : 'Disabled'} />
        </SnapshotSection>

        <SnapshotSection icon={HardDrive} title="Storage" summary={`${storageGb} GB across ${request.hardware.disks.length} disk${request.hardware.disks.length === 1 ? '' : 's'}`}>
          {request.hardware.disks.map((disk, index) => (
            <DetailLine
              key={`${index}-${disk.size_gb}-${disk.datastore_id ?? 'automatic'}`}
              label={`Disk ${index + 1}`}
              value={`${disk.size_gb} GB · ${disk.provisioning === 'thin' ? 'Thin provisioned' : 'Thick provisioned'} · ${labels.datastore(disk.datastore_id)}`}
            />
          ))}
        </SnapshotSection>

        <SnapshotSection icon={Network} title="Network" summary={`${labels.network} · ${humanizeIdentifier(request.network.mode)}`}>
          <DetailLine label="Port group" value={labels.network} />
          <DetailLine label="Addressing" value={humanizeIdentifier(request.network.mode)} />
          <DetailLine label="IPv4 address" value={networkAddress} mono={request.network.mode === 'STATIC'} />
          {request.network.ipv4 && (
            <>
              <DetailLine label="Gateway" value={request.network.ipv4.gateway} mono />
              <DetailLine label="DNS servers" value={request.network.ipv4.dns_servers.join(', ') || 'Platform default'} mono />
            </>
          )}
          <DetailLine label="Adapter" value={request.network.adapter_type === 'VMXNET3' ? 'VMXNET 3' : 'Intel E1000E'} />
        </SnapshotSection>

        <SnapshotSection icon={PackageCheck} title="Configuration packages" summary={`${request.application_ids.length} application${request.application_ids.length === 1 ? '' : 's'} · ${request.certificate_package_ids.length} certificate package${request.certificate_package_ids.length === 1 ? '' : 's'}`}>
          <DetailLine label="Applications" value={labels.applicationNames.join(', ') || 'None selected'} />
          <DetailLine label="Certificates" value={labels.certificateNames.join(', ') || 'None selected'} />
          <DetailLine label="Domain join" value={request.guest.domain_join?.domain ?? 'Not requested'} />
          <DetailLine label="Description" value={request.vm.description || 'No description'} />
        </SnapshotSection>
      </div>
    </ConsolePanel>
  )
}

function FinalValidationPanel({ step }: { step: JobStepOut }) {
  const checklist = (step.artifacts?.['checklist'] as ChecklistItem[] | undefined) ?? []
  if (checklist.length === 0) return null

  const groups = checklist.reduce<Record<string, ChecklistItem[]>>((accumulator, item) => {
    accumulator[item.group] = accumulator[item.group] ?? []
    accumulator[item.group].push(item)
    return accumulator
  }, {})
  const passed = checklist.filter((item) => item.status === 'PASS').length
  const allPassed = passed === checklist.length

  return (
    <ConsolePanel className={allPassed ? 'border-emerald-300' : 'border-amber-300'}>
      <PanelHeader
        title="Final validation"
        description="Post-deployment checks recorded by the execution engine."
        actions={<Badge tone={allPassed ? 'success' : 'warning'}>{passed} of {checklist.length} passed</Badge>}
      />
      <div className="grid gap-3 p-4 md:grid-cols-2">
        {Object.entries(groups).map(([group, items]) => (
          <section key={group} className="rounded-xl border border-[#dfe4de] bg-[#fafbf8] p-4">
            <h4 className="text-[10px] font-bold uppercase tracking-[0.11em] text-[#68736d]">{humanizeIdentifier(group)}</h4>
            <ul className="mt-3 space-y-3">
              {items.map((item) => (
                <li key={item.label} className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-[#303a34]">{item.label}</p>
                    {item.detail && <p className="mt-0.5 text-[11px] leading-4 text-[#68736d]">{item.detail}</p>}
                  </div>
                  <CheckStatusBadge status={item.status} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </ConsolePanel>
  )
}

function StageRow({
  step,
  isAdmin,
  canRetry,
  onRetry,
  retryPending,
  isLast,
}: {
  step: JobStepOut
  isAdmin: boolean
  canRetry: boolean
  onRetry: (stageKey: string) => void
  retryPending: boolean
  isLast: boolean
}) {
  const hasTechnicalOutput = Boolean(step.output || (isAdmin && step.error_technical))

  return (
    <li className="relative grid grid-cols-[34px_minmax(0,1fr)] gap-3 pb-3 last:pb-0">
      {!isLast && <span className="absolute bottom-0 left-[16px] top-8 w-px bg-[#dfe4de]" aria-hidden />}
      <span className={cn(
        'relative z-10 grid h-[34px] w-[34px] place-items-center rounded-full border bg-white',
        step.status === 'FAILED'
          ? 'border-red-300'
          : step.status === 'RUNNING'
            ? 'border-[#efad85] shadow-[0_0_0_3px_rgba(229,107,63,0.12)]'
            : 'border-[#d8ddd7]',
      )}>
        <StepStatusIcon status={step.status} />
      </span>

      <article className={cn(
        'overflow-hidden rounded-xl border bg-white',
        step.status === 'FAILED'
          ? 'border-red-200'
          : step.status === 'RUNNING'
            ? 'border-[#efc2a6]'
            : 'border-[#dfe4de]',
      )}>
        <header className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] font-semibold tabular-nums text-[#8a938d]">
                {String(step.sequence).padStart(2, '0')}
              </span>
              <h4 className="text-xs font-semibold tracking-[-0.01em] text-[#202923]">
                {step.name || humanizeStageKey(step.stage_key)}
              </h4>
            </div>
            <p className="mt-1 text-[11px] tabular-nums text-[#68736d]">{stageTiming(step)}</p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            {(step.attempt > 1 || step.max_attempts > 1) && (
              <span className="text-[10px] font-medium text-[#68736d]">
                Attempt {step.attempt} of {step.max_attempts}
              </span>
            )}
            <Badge tone={STEP_TONES[step.status]}>{humanizeIdentifier(step.status)}</Badge>
            {canRetry && step.status === 'FAILED' && step.retryable && (
              <Button size="sm" variant="secondary" onClick={() => onRetry(step.stage_key)} loading={retryPending}>
                <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Retry stage
              </Button>
            )}
          </div>
        </header>

        {step.error_human && (
          <div className="border-t border-red-100 bg-red-50/70 px-4 py-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-red-700">What happened</p>
            <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-red-900">{step.error_human}</p>
          </div>
        )}

        {hasTechnicalOutput && (
          <details className="group border-t border-[#e2e6e1] bg-[#f8f9f6]">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-[11px] font-semibold text-[#59635d] hover:text-brand-700">
              <Terminal className="h-3.5 w-3.5" aria-hidden />
              View technical output
              <ChevronDown className="ml-auto h-4 w-4 transition-transform group-open:rotate-180" aria-hidden />
            </summary>
            <div className="space-y-3 border-t border-[#e2e6e1] bg-white px-4 py-3">
              {step.output && (
                <div>
                  <p className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-[#68736d]">Stage log</p>
                  <LogViewer text={step.output} ariaLabel={`${step.name} technical output`} />
                </div>
              )}
              {isAdmin && step.error_technical && (
                <div>
                  <p className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-[#68736d]">Administrator diagnostics</p>
                  <LogViewer text={step.error_technical} className="max-h-48" ariaLabel="Administrator technical error detail" />
                </div>
              )}
            </div>
          </details>
        )}
      </article>
    </li>
  )
}

export function JobDetailPage() {
  const { jobId = '' } = useParams()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const isAdmin = Boolean(user?.permissions.includes('admin.settings'))
  const canRetry = Boolean(user?.permissions.includes('jobs.retry'))
  const canCancel = Boolean(user?.permissions.includes('jobs.cancel'))

  const jobQuery = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.job(jobId),
    refetchInterval: (query) => {
      const payload = query.state.data as JobDetailOut | undefined
      return payload && (payload.status === 'RUNNING' || payload.status === 'QUEUED') ? 10_000 : false
    },
  })
  const job = jobQuery.data

  useJobEvents(jobId, job?.status === 'RUNNING' || job?.status === 'QUEUED')

  const retryAll = useMutation({
    mutationFn: () => api.retryJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['job', jobId] }),
  })
  const retryStage = useMutation({
    mutationFn: (stageKey: string) => api.retryJob(jobId, stageKey),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['job', jobId] }),
  })
  const cancel = useMutation({
    mutationFn: () => api.cancelJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['job', jobId] }),
  })

  if (jobQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Link to="/jobs" className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-700 hover:underline">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Back to deployments
        </Link>
        <LoadingState title="Loading deployment" description="Retrieving configuration, stages, and the latest execution state." />
      </div>
    )
  }

  if (jobQuery.isError || !job) {
    return (
      <div className="space-y-4">
        <Link to="/jobs" className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-700 hover:underline">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Back to deployments
        </Link>
        <EmptyState
          title="Deployment could not be loaded"
          description="The deployment may no longer be available, or the operations service could not be reached."
          action={<Button size="sm" variant="secondary" onClick={() => void jobQuery.refetch()}>Try again</Button>}
        />
      </div>
    )
  }

  const orderedSteps = [...job.steps].sort((left, right) => left.sequence - right.sequence)
  const failedSteps = orderedSteps.filter((step) => step.status === 'FAILED' && step.retryable)
  const finalStep = orderedSteps.find((step) => step.stage_key === 'final_validation')
  const succeededSteps = orderedSteps.filter((step) => step.status === 'SUCCEEDED').length
  const failedCount = orderedSteps.filter((step) => step.status === 'FAILED').length
  const isActive = job.status === 'RUNNING' || job.status === 'QUEUED'
  const actionError = retryAll.error ?? retryStage.error ?? cancel.error

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <Link to="/jobs" className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-700 hover:underline">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Back to deployments
      </Link>

      <PageHeader
        eyebrow="Deployment detail"
        title={job.vm_name}
        description={`Requested by ${job.requested_by_username ?? 'system automation'} and queued ${formatDateTime(job.queued_at)}.`}
        actions={(
          <>
            {(job.status === 'FAILED' || job.status === 'PARTIALLY_COMPLETED') && canRetry && failedSteps.length > 0 && (
              <Button size="sm" loading={retryAll.isPending} onClick={() => retryAll.mutate()}>
                <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Retry failed stages
              </Button>
            )}
            {isActive && canCancel && !job.cancel_requested && (
              <Button size="sm" variant="danger" loading={cancel.isPending} onClick={() => cancel.mutate()}>
                <XCircle className="h-3.5 w-3.5" aria-hidden /> Cancel deployment
              </Button>
            )}
          </>
        )}
        meta={(
          <>
            <JobStatusBadge status={job.status} />
            <span>{job.datacenter_name ?? 'Datacenter not available'}</span>
            <span>{orderedSteps.length} execution stages</span>
            {isActive && (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#e56b3f]" /> Live updates enabled
              </span>
            )}
          </>
        )}
      />

      {actionError && (
        <Alert tone="danger" title="Deployment action could not be completed">
          {mutationMessage(actionError)}
        </Alert>
      )}

      <ConsolePanel>
        <div className="grid xl:grid-cols-[minmax(0,1.6fr)_minmax(420px,1fr)]">
          <div className="border-b border-[#e2e6e1] p-5 xl:border-b-0 xl:border-r">
            <div className="mb-4 flex items-start justify-between gap-5">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.11em] text-[#7b857f]">Execution progress</p>
                <p className="mt-1.5 text-base font-semibold tracking-[-0.02em] text-[#202923]">
                  {job.current_stage
                    ? humanizeStageKey(job.current_stage)
                    : job.status === 'COMPLETED'
                      ? 'Deployment complete'
                      : job.status === 'QUEUED'
                        ? 'Waiting for an execution slot'
                        : 'No active stage'}
                </p>
                <p className="mt-1 text-xs leading-5 text-[#68736d]">
                  {isActive ? 'This view updates automatically while work is active.' : 'This execution has reached a terminal state.'}
                </p>
              </div>
              <span className="font-mono text-3xl font-semibold tabular-nums tracking-[-0.04em] text-[#17201c]">{job.progress}%</span>
            </div>
            <ProgressBar percent={job.progress} label={`${succeededSteps} of ${orderedSteps.length} stages succeeded`} />
            {job.cancel_requested && isActive && (
              <div className="mt-4">
                <Alert tone="warning" title="Cancellation requested">
                  Execution will stop at the next safe stage boundary.
                </Alert>
              </div>
            )}
            {job.error_summary && (
              <div className="mt-4">
                <Alert tone="danger" title="Deployment needs attention">
                  {job.error_summary}
                  {job.status === 'PARTIALLY_COMPLETED' && (
                    <p className="mt-1">The virtual machine exists. Retry only the recoverable failed stages below.</p>
                  )}
                </Alert>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 divide-x divide-y divide-[#e2e6e1]">
            <DataPoint label="Started" value={job.started_at ? formatTime(job.started_at) : 'Not started'} detail={job.started_at ? formatDateTime(job.started_at) : 'Waiting in queue'} />
            <DataPoint label="Duration" value={formatDuration(job.duration_seconds)} detail={job.finished_at ? `Finished ${formatTime(job.finished_at)}` : 'Live execution time'} />
            <DataPoint label="Succeeded" value={succeededSteps} detail={`${orderedSteps.length} total stages`} />
            <DataPoint label="Needs attention" value={failedCount} detail={`${failedSteps.length} retryable`} />
          </div>
        </div>
      </ConsolePanel>

      {job.request_payload ? (
        <RequestSnapshot request={job.request_payload} datacenterName={job.datacenter_name} />
      ) : (
        <EmptyState
          title="Submitted configuration is unavailable"
          description="This historical deployment does not include a readable request snapshot."
        />
      )}

      {finalStep && <FinalValidationPanel step={finalStep} />}

      <ConsolePanel>
        <PanelHeader
          title="Execution pipeline"
          description="Ordered stages, attempts, timings, operator guidance, and retry controls."
          actions={(
            <span className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-[#68736d]">
              <Clock3 className="h-3.5 w-3.5" aria-hidden />
              {isActive ? 'Updating live' : 'Execution record'}
            </span>
          )}
        />
        {orderedSteps.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No stage activity recorded" description="Stages will appear when execution begins." />
          </div>
        ) : (
          <ol className="p-4 sm:p-5">
            {orderedSteps.map((step, index) => (
              <StageRow
                key={step.id}
                step={step}
                isAdmin={isAdmin}
                canRetry={canRetry}
                onRetry={(stageKey) => retryStage.mutate(stageKey)}
                retryPending={retryStage.isPending && retryStage.variables === step.stage_key}
                isLast={index === orderedSteps.length - 1}
              />
            ))}
          </ol>
        )}
      </ConsolePanel>
    </div>
  )
}
