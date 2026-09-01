import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  RotateCcw,
  Terminal,
  XCircle,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { Alert, Badge, JobStatusBadge, ProgressBar, Spinner, StepStatusIcon } from '@/components/ui/feedback'
import { LogViewer } from '@/components/ui/log-viewer'
import { ConsolePanel, DataPoint, PageHeader, PanelHeader } from '@/components/ui/page'
import { Button } from '@/components/ui/button'
import { useJobEvents } from '@/features/jobs/useJobEvents'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatDateTime, formatDuration, formatTime, humanizeStageKey } from '@/lib/utils'
import type { JobDetailOut, JobStepOut, StepStatus } from '@/types/api'

interface ChecklistItem {
  group: string
  label: string
  status: string
  detail?: string
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
  if (!step.started_at) return 'Not started'
  const start = formatTime(step.started_at)
  if (!step.finished_at) return `${start} → running`
  const durationSeconds = Math.max(
    0,
    (new Date(step.finished_at).getTime() - new Date(step.started_at).getTime()) / 1000,
  )
  return `${start} → ${formatTime(step.finished_at)} · ${formatDuration(durationSeconds)}`
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

  return (
    <ConsolePanel className="border-emerald-300">
      <PanelHeader
        title="Final validation"
        description="Post-provision checks recorded by the execution engine"
        actions={<Badge tone={passed === checklist.length ? 'success' : 'warning'}>{passed}/{checklist.length} passed</Badge>}
      />
      <div className="grid divide-y divide-slate-200 sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        {Object.entries(groups).map(([group, items]) => (
          <div key={group} className="p-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-slate-500">{group}</p>
            <ul className="mt-2 space-y-2">
              {items.map((item) => {
                const itemPassed = item.status === 'PASS'
                return (
                  <li key={item.label} className="flex items-start gap-2 text-xs">
                    {itemPassed
                      ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" aria-hidden />
                      : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" aria-hidden />}
                    <span>
                      <span className="font-medium text-slate-800">{item.label}</span>
                      {item.detail && <span className="mt-0.5 block text-[11px] text-slate-500">{item.detail}</span>}
                    </span>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </div>
    </ConsolePanel>
  )
}

function StageRow({
  step,
  isAdmin,
  onRetry,
  retryPending,
}: {
  step: JobStepOut
  isAdmin: boolean
  onRetry: (stageKey: string) => void
  retryPending: boolean
}) {
  const [expanded, setExpanded] = useState(step.status === 'FAILED')
  const hasDetails = Boolean(step.output || step.error_human || (isAdmin && step.error_technical))

  return (
    <li className={cn('border-l-2 bg-white', step.status === 'FAILED' ? 'border-l-red-500' : step.status === 'RUNNING' ? 'border-l-brand-500' : 'border-l-transparent')}>
      <div className="flex min-w-0 items-center border-b border-slate-200">
        <button
          type="button"
          onClick={() => hasDetails && setExpanded((value) => !value)}
          disabled={!hasDetails}
          className={cn(
            'grid min-w-0 flex-1 grid-cols-[42px_24px_minmax(0,1fr)] items-center gap-2 px-3 py-2.5 text-left',
            hasDetails && 'hover:bg-slate-50',
          )}
          aria-expanded={hasDetails ? expanded : undefined}
        >
          <span className="font-mono text-[10px] font-semibold tabular-nums text-slate-400">
            {String(step.sequence).padStart(2, '0')}
          </span>
          <StepStatusIcon status={step.status} />
          <span className="min-w-0">
            <span className={cn('block truncate text-xs font-semibold', step.status === 'FAILED' ? 'text-red-700' : 'text-slate-800')}>
              {step.name}
            </span>
            <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">{stageTiming(step)}</span>
          </span>
        </button>

        <div className="flex shrink-0 items-center gap-2 px-3">
          {step.attempt > 1 && <span className="font-mono text-[10px] text-slate-500">attempt {step.attempt}/{step.max_attempts}</span>}
          <Badge tone={STEP_TONES[step.status]}>{step.status.toLowerCase()}</Badge>
          {step.status === 'FAILED' && step.retryable && (
            <Button size="sm" variant="secondary" onClick={() => onRetry(step.stage_key)} loading={retryPending}>
              <RotateCcw className="h-3.5 w-3.5" /> Retry
            </Button>
          )}
          {hasDetails && (
            <button
              type="button"
              aria-label={`${expanded ? 'Collapse' : 'Expand'} ${step.name}`}
              onClick={() => setExpanded((value) => !value)}
              className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            >
              {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>
          )}
        </div>
      </div>

      {expanded && hasDetails && (
        <div className="space-y-3 border-b border-slate-200 bg-[#f8fafb] px-4 py-3 pl-[86px]">
          {step.error_human && (
            <Alert tone="danger" title="Operator diagnosis">
              <span className="whitespace-pre-wrap">{step.error_human}</span>
            </Alert>
          )}
          {step.output && (
            <div>
              <p className="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-slate-500">
                <Terminal className="h-3.5 w-3.5" aria-hidden /> Stage output
              </p>
              <LogViewer text={step.output} ariaLabel={`${step.name} output`} />
            </div>
          )}
          {isAdmin && step.error_technical && (
            <details className="rounded border border-slate-300 bg-white px-3 py-2 text-xs text-slate-600">
              <summary className="cursor-pointer select-none font-semibold">Administrator technical detail</summary>
              <LogViewer text={step.error_technical} className="mt-2 max-h-48" ariaLabel="Technical error detail" />
            </details>
          )}
        </div>
      )}
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

  if (jobQuery.isLoading) return <div className="flex h-64 items-center justify-center"><Spinner /></div>
  if (jobQuery.isError || !job) {
    return (
      <Alert tone="danger" title="Job unavailable">
        The provisioning job could not be loaded.{' '}
        <Button size="sm" variant="secondary" onClick={() => void jobQuery.refetch()}>Retry</Button>
      </Alert>
    )
  }

  const failedSteps = job.steps.filter((step) => step.status === 'FAILED' && step.retryable)
  const finalStep = job.steps.find((step) => step.stage_key === 'final_validation')
  const succeededSteps = job.steps.filter((step) => step.status === 'SUCCEEDED').length
  const request = job.request_payload

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <PageHeader
        eyebrow="Execution detail"
        title={job.vm_name}
        description={`Provisioning job ${job.id.slice(0, 8)} requested by ${job.requested_by_username ?? 'unknown operator'}.`}
        actions={(
          <>
            {(job.status === 'FAILED' || job.status === 'PARTIALLY_COMPLETED') && canRetry && failedSteps.length > 0 && (
              <Button size="sm" loading={retryAll.isPending} onClick={() => retryAll.mutate()}>
                <RotateCcw className="h-3.5 w-3.5" /> Retry failed stages
              </Button>
            )}
            {(job.status === 'QUEUED' || job.status === 'RUNNING') && canCancel && !job.cancel_requested && (
              <Button size="sm" variant="danger" loading={cancel.isPending} onClick={() => cancel.mutate()}>
                <XCircle className="h-3.5 w-3.5" /> Cancel job
              </Button>
            )}
          </>
        )}
        meta={(
          <>
            <JobStatusBadge status={job.status} />
            <span className="font-mono">ID {job.id}</span>
            <span>Queued {formatDateTime(job.queued_at)}</span>
          </>
        )}
      />

      <ConsolePanel>
        <div className="grid xl:grid-cols-[minmax(0,1.7fr)_minmax(420px,1fr)]">
          <div className="border-b border-slate-200 p-4 xl:border-b-0 xl:border-r">
            <div className="mb-3 flex items-start justify-between gap-4">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-slate-500">Execution progress</p>
                <p className="mt-1 text-sm font-semibold text-slate-900">
                  {job.current_stage ? humanizeStageKey(job.current_stage) : job.status === 'COMPLETED' ? 'Execution complete' : 'Awaiting stage assignment'}
                </p>
              </div>
              <span className="font-mono text-xl font-semibold tabular-nums text-slate-950">{job.progress}%</span>
            </div>
            <ProgressBar percent={job.progress} label={`${succeededSteps} of ${job.steps.length} stages succeeded`} />
            {job.cancel_requested && job.status === 'RUNNING' && (
              <div className="mt-3"><Alert tone="warning">Cancellation is pending and will apply at the next safe stage boundary.</Alert></div>
            )}
            {job.error_summary && (
              <div className="mt-3">
                <Alert tone="danger" title="Provisioning problem">
                  {job.error_summary}
                  {job.status === 'PARTIALLY_COMPLETED' && <p className="mt-1 text-xs">The VM exists; retry only the failed recoverable stages below.</p>}
                </Alert>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 divide-x divide-y divide-slate-200">
            <DataPoint label="Started" value={job.started_at ? formatTime(job.started_at) : '—'} detail={formatDateTime(job.started_at)} />
            <DataPoint label="Duration" value={formatDuration(job.duration_seconds)} detail={job.finished_at ? `Finished ${formatTime(job.finished_at)}` : 'Live execution time'} />
            <DataPoint label="Succeeded" value={succeededSteps} detail={`${job.steps.length} total stages`} />
            <DataPoint label="Failed" value={job.steps.filter((step) => step.status === 'FAILED').length} detail={`${failedSteps.length} retryable`} />
          </div>
        </div>
      </ConsolePanel>

      {request && (
        <ConsolePanel>
          <PanelHeader title="Request snapshot" description="Immutable placement and sizing values submitted with this job" />
          <div className="grid divide-y divide-slate-200 sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-5">
            <DataPoint label="Source" value={request.source_type === 'template' ? 'Template' : 'Blank VM'} detail={request.guest.template_id ?? 'No guest source'} />
            <DataPoint label="vCenter" value={request.compute.vcenter_id.slice(0, 8)} detail={`DC ${request.compute.datacenter_id}`} />
            <DataPoint label="Compute" value={`${request.hardware.cpu} vCPU`} detail={`${Math.round(request.hardware.memory_mb / 1024)} GB memory`} />
            <DataPoint label="Storage" value={`${request.hardware.disks.reduce((sum, disk) => sum + disk.size_gb, 0)} GB`} detail={`${request.hardware.disks.length} virtual disk(s)`} />
            <DataPoint label="Network" value={request.network.mode} detail={request.network.network_id} />
          </div>
        </ConsolePanel>
      )}

      {finalStep?.status === 'SUCCEEDED' && <FinalValidationPanel step={finalStep} />}

      <ConsolePanel>
        <PanelHeader
          title="Execution pipeline"
          description="Stage order, attempts, timing, operator output, and administrator diagnostics"
          actions={<span className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500"><Clock3 className="h-3.5 w-3.5" /> Live while active</span>}
        />
        <ol>
          {job.steps.map((step) => (
            <StageRow
              key={step.id}
              step={step}
              isAdmin={isAdmin}
              onRetry={(stageKey) => retryStage.mutate(stageKey)}
              retryPending={retryStage.isPending}
            />
          ))}
        </ol>
      </ConsolePanel>

      <Link to="/jobs" className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-700 hover:underline">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to provisioning jobs
      </Link>
    </div>
  )
}
