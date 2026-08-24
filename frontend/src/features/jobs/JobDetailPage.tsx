import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, RotateCcw, XCircle } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { useJobEvents } from '@/features/jobs/useJobEvents'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Alert,
  Badge,
  JobStatusBadge,
  ProgressBar,
  Spinner,
  StepStatusIcon,
} from '@/components/ui/feedback'
import { LogViewer } from '@/components/ui/log-viewer'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatDateTime, formatDuration, formatTime } from '@/lib/utils'
import type { JobDetailOut, JobStepOut } from '@/types/api'

interface ChecklistItem {
  group: string
  label: string
  status: string
  detail?: string
}

function FinalValidationCard({ step }: { step: JobStepOut }) {
  const checklist = (step.artifacts?.['checklist'] as ChecklistItem[] | undefined) ?? []
  if (checklist.length === 0) return null

  const groups = checklist.reduce<Record<string, ChecklistItem[]>>((accumulator, item) => {
    accumulator[item.group] = accumulator[item.group] ?? []
    accumulator[item.group].push(item)
    return accumulator
  }, {})

  return (
    <Card className="border-emerald-200">
      <CardHeader>
        <CardTitle>Final Validation</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {Object.entries(groups).map(([group, items]) => (
          <div key={group}>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{group}</p>
            <ul className="space-y-1">
              {items.map((item) => (
                <li key={item.label} className="flex items-start gap-1.5 text-sm">
                  <span
                    className={
                      item.status === 'PASS'
                        ? 'text-emerald-600'
                        : 'text-red-600'
                    }
                    aria-hidden
                  >
                    {item.status === 'PASS' ? '✓' : '✗'}
                  </span>
                  <span className="text-slate-700">{item.label}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function StepRow({
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
    <li className="rounded-md border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => hasDetails && setExpanded((value) => !value)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left"
        aria-expanded={expanded}
      >
        <StepStatusIcon status={step.status} />
        <span className="min-w-0 flex-1">
          <span className={`block truncate text-sm ${step.status === 'FAILED' ? 'font-medium text-red-700' : 'text-slate-800'}`}>
            {step.name}
          </span>
          <span className="block text-xs text-slate-400">
            {step.started_at ? `${formatTime(step.started_at)}${step.finished_at ? ` → ${formatTime(step.finished_at)}` : ' → running'}` : 'Not started'}
            {step.attempt > 1 ? ` · attempt ${step.attempt}` : ''}
          </span>
        </span>
        {step.status === 'SKIPPED' && <Badge tone="neutral">Skipped</Badge>}
        {step.status === 'FAILED' && step.retryable && (
          <Button
            size="sm"
            variant="secondary"
            onClick={(event) => {
              event.stopPropagation()
              onRetry(step.stage_key)
            }}
            loading={retryPending}
          >
            <RotateCcw className="h-3.5 w-3.5" /> Retry
          </Button>
        )}
        {hasDetails &&
          (expanded ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />)}
      </button>

      {expanded && hasDetails && (
        <div className="space-y-2 border-t border-slate-100 px-3 py-2">
          {step.output && <LogViewer text={step.output} ariaLabel={`${step.name} output`} />}
          {step.error_human && (
            <Alert tone="danger" title="Stage failed">
              <span className="whitespace-pre-wrap">{step.error_human}</span>
            </Alert>
          )}
          {isAdmin && step.error_technical && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer select-none">Technical details (administrators)</summary>
              <LogViewer text={step.error_technical} className="mt-1 max-h-40" ariaLabel="Technical error detail" />
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

  const { data: job, isLoading, isError } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.job(jobId),
    refetchInterval: (query) => {
      const payload = query.state.data as JobDetailOut | undefined
      return payload && (payload.status === 'RUNNING' || payload.status === 'QUEUED') ? 10_000 : false
    },
  })

  // Live updates via SSE while the job is in flight.
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

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    )
  }
  if (isError || !job) {
    return <Alert tone="danger" title="Job not found">The provisioning job could not be loaded.</Alert>
  }

  const failedSteps = job.steps.filter((step) => step.status === 'FAILED' && step.retryable)
  const finalStep = job.steps.find((step) => step.stage_key === 'final_validation')

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      {/* Header */}
      <Card>
        <CardContent className="py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold text-slate-900">{job.vm_name}</h2>
                <JobStatusBadge status={job.status} />
              </div>
              <p className="mt-0.5 text-xs text-slate-500">
                Job {job.id.slice(0, 8)} · requested by {job.requested_by_username ?? 'unknown'} ·{' '}
                queued {formatDateTime(job.queued_at)}
                {job.duration_seconds != null ? ` · duration ${formatDuration(job.duration_seconds)}` : ''}
              </p>
            </div>
            <div className="flex gap-2">
              {(job.status === 'FAILED' || job.status === 'PARTIALLY_COMPLETED') && canRetry && failedSteps.length > 0 && (
                <Button size="sm" loading={retryAll.isPending} onClick={() => retryAll.mutate()}>
                  <RotateCcw className="h-3.5 w-3.5" /> Retry failed stage(s)
                </Button>
              )}
              {(job.status === 'QUEUED' || job.status === 'RUNNING') && canCancel && !job.cancel_requested && (
                <Button size="sm" variant="danger" loading={cancel.isPending} onClick={() => cancel.mutate()}>
                  <XCircle className="h-3.5 w-3.5" /> Cancel
                </Button>
              )}
            </div>
          </div>

          <div className="mt-4">
            <ProgressBar
              percent={job.progress}
              label={`${job.progress}%${job.current_stage ? ` · current stage: ${job.current_stage.replaceAll('_', ' ')}` : ''}`}
            />
          </div>

          {job.cancel_requested && job.status === 'RUNNING' && (
            <div className="mt-3">
              <Alert tone="warning">Cancellation requested — the job will stop at the next stage boundary.</Alert>
            </div>
          )}
          {job.error_summary && (
            <div className="mt-3">
              <Alert tone="danger" title="Provisioning problem">
                {job.error_summary}
                {job.status === 'PARTIALLY_COMPLETED' && (
                  <p className="mt-1 text-xs">
                    The VM was created successfully — safe retries are available for the failed stages below.
                  </p>
                )}
              </Alert>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Final validation result */}
      {finalStep && finalStep.status === 'SUCCEEDED' && <FinalValidationCard step={finalStep} />}

      {/* Execution timeline */}
      <Card>
        <CardHeader>
          <CardTitle>Execution Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1.5">
            {job.steps.map((step) => (
              <StepRow
                key={step.id}
                step={step}
                isAdmin={isAdmin}
                onRetry={(stageKey) => retryStage.mutate(stageKey)}
                retryPending={retryStage.isPending}
              />
            ))}
          </ul>
        </CardContent>
      </Card>

      <Link to="/jobs" className="inline-block text-xs font-medium text-brand-600 hover:underline">
        ← Back to all jobs
      </Link>
    </div>
  )
}

