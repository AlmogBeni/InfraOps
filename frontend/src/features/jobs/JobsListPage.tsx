import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ChevronRight,
  CircleAlert,
  Plus,
  RefreshCw,
  Search,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { EmptyState, JobStatusBadge, LoadingState } from '@/components/ui/feedback'
import { Input, Select } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatDateTime, formatDuration, humanizeStageKey } from '@/lib/utils'
import type { JobListResponse, JobOut } from '@/types/api'

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'All statuses' },
  { value: 'QUEUED', label: 'Queued' },
  { value: 'RUNNING', label: 'Running' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'PARTIALLY_COMPLETED', label: 'Partially completed' },
  { value: 'FAILED', label: 'Failed' },
  { value: 'CANCELLED', label: 'Cancelled' },
]

const PAGE_SIZE = 25

function executionLabel(job: JobOut): string {
  if (job.current_stage) return humanizeStageKey(job.current_stage)
  if (job.status === 'COMPLETED') return 'All stages completed'
  if (job.status === 'FAILED') return 'Stopped after an error'
  if (job.status === 'PARTIALLY_COMPLETED') return 'Completed with follow-up required'
  if (job.status === 'CANCELLED') return 'Execution cancelled'
  return job.status === 'QUEUED' ? 'Waiting for an execution slot' : 'Preparing next stage'
}

function ProgressCell({ job }: { job: JobOut }) {
  return (
    <div className="min-w-[180px]">
      <div className="flex items-center justify-between gap-3">
        <span className="truncate text-xs font-medium text-[#364039]">{executionLabel(job)}</span>
        <span className="font-mono text-[10px] font-semibold tabular-nums text-[#68736d]">
          {job.progress}%
        </span>
      </div>
      <div
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#e3e7e2]"
        role="progressbar"
        aria-label={`${job.vm_name} deployment progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.max(0, Math.min(100, job.progress))}
      >
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-300',
            job.status === 'FAILED'
              ? 'bg-red-500'
              : job.status === 'PARTIALLY_COMPLETED'
                ? 'bg-amber-500'
                : 'bg-brand-600',
          )}
          style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }}
        />
      </div>
    </div>
  )
}

export function JobsListPage() {
  const navigate = useNavigate()
  const { hasPermission } = useAuth()
  const canProvision = hasPermission('provisioning.submit')
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [vmNameFilter, setVmNameFilter] = useState('')

  const jobs = useQuery({
    queryKey: ['jobs', page, statusFilter, vmNameFilter],
    queryFn: () =>
      api.jobs({
        page,
        page_size: PAGE_SIZE,
        status: statusFilter || undefined,
        vm_name: vmNameFilter || undefined,
      }),
    refetchInterval: (query) => {
      const payload = query.state.data as JobListResponse | undefined
      const hasActive = payload?.items.some(
        (job) => job.status === 'RUNNING' || job.status === 'QUEUED',
      )
      return hasActive ? 5_000 : 30_000
    },
  })

  const data = jobs.data
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1
  const hasFilters = Boolean(statusFilter || vmNameFilter)
  const activeOnPage = data?.items.filter(
    (job) => job.status === 'RUNNING' || job.status === 'QUEUED',
  ).length ?? 0
  const attentionOnPage = data?.items.filter(
    (job) => job.status === 'FAILED' || job.status === 'PARTIALLY_COMPLETED',
  ).length ?? 0

  function clearFilters() {
    setStatusFilter('')
    setVmNameFilter('')
    setPage(1)
  }

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Operations"
        title="VM deployments"
        description="Monitor every virtual machine request from queue admission through final validation, and recover failed stages without losing execution context."
        actions={(
          <>
            <Button
              size="sm"
              variant="secondary"
              loading={jobs.isFetching && !jobs.isLoading}
              onClick={() => void jobs.refetch()}
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Refresh
            </Button>
            {canProvision && (
              <Button size="sm" onClick={() => navigate('/provisioning/new')}>
                <Plus className="h-3.5 w-3.5" aria-hidden /> New VM request
              </Button>
            )}
          </>
        )}
        meta={(
          <>
            <span>{data?.total ?? 0} deployment{data?.total === 1 ? '' : 's'} in this view</span>
            <span className="inline-flex items-center gap-1.5">
              <span className={cn('h-1.5 w-1.5 rounded-full', activeOnPage ? 'animate-pulse bg-[#e56b3f]' : 'bg-emerald-500')} />
              {activeOnPage ? `${activeOnPage} active on this page` : 'No active work on this page'}
            </span>
            {attentionOnPage > 0 && (
              <span className="inline-flex items-center gap-1.5 text-amber-800">
                <CircleAlert className="h-3.5 w-3.5" aria-hidden />
                {attentionOnPage} need{attentionOnPage === 1 ? 's' : ''} attention
              </span>
            )}
          </>
        )}
      />

      <div className="console-toolbar">
        <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
          <label className="field-label" htmlFor="jobs-vm-filter">Virtual machine</label>
          <Search className="pointer-events-none absolute bottom-3 left-3 h-4 w-4 text-[#87908a]" aria-hidden />
          <Input
            id="jobs-vm-filter"
            className="pl-9"
            placeholder="Search by VM name"
            value={vmNameFilter}
            onChange={(event) => {
              setPage(1)
              setVmNameFilter(event.target.value)
            }}
          />
        </div>
        <div className="w-full sm:w-52">
          <label className="field-label" htmlFor="jobs-status-filter">Execution status</label>
          <Select
            id="jobs-status-filter"
            value={statusFilter}
            onChange={(event) => {
              setPage(1)
              setStatusFilter(event.target.value)
            }}
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </Select>
        </div>
        <Button size="sm" variant="ghost" disabled={!hasFilters} onClick={clearFilters}>
          Clear filters
        </Button>
      </div>

      {jobs.isLoading ? (
        <LoadingState
          title="Loading deployments"
          description="Retrieving the latest queue and execution state."
        />
      ) : jobs.isError ? (
        <EmptyState
          title="Deployments could not be loaded"
          description="The operations service did not return the deployment queue. Try again to reconnect."
          action={<Button size="sm" variant="secondary" onClick={() => void jobs.refetch()}>Try again</Button>}
        />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title={hasFilters ? 'No deployments match these filters' : 'No deployments yet'}
          description={hasFilters ? 'Clear the filters to return to the full queue.' : 'Submit a VM request to start the first deployment.'}
          action={hasFilters
            ? <Button size="sm" variant="secondary" onClick={clearFilters}>Clear filters</Button>
            : canProvision
              ? <Button size="sm" onClick={() => navigate('/provisioning/new')}><Plus className="h-3.5 w-3.5" /> New VM request</Button>
              : undefined}
        />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Virtual machine</Th>
                <Th>Execution</Th>
                <Th>Status</Th>
                <Th>Datacenter</Th>
                <Th>Requested by</Th>
                <Th>Queued</Th>
                <Th>Duration</Th>
                <Th><span className="sr-only">Open deployment</span></Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((job) => (
                <Tr key={job.id} clickable onClick={() => navigate(`/jobs/${job.id}`)}>
                  <Td className="min-w-[190px]">
                    <Link
                      to={`/jobs/${job.id}`}
                      onClick={(event) => event.stopPropagation()}
                      className="font-semibold tracking-[-0.01em] text-[#202923] hover:text-brand-700 hover:underline"
                    >
                      {job.vm_name}
                    </Link>
                    {job.error_summary && (
                      <p className="mt-1 max-w-[260px] truncate text-[11px] text-red-700" title={job.error_summary}>
                        {job.error_summary}
                      </p>
                    )}
                  </Td>
                  <Td><ProgressCell job={job} /></Td>
                  <Td><JobStatusBadge status={job.status} /></Td>
                  <Td className="whitespace-nowrap text-xs font-medium text-[#4f5a53]">
                    {job.datacenter_name ?? 'Not available'}
                  </Td>
                  <Td className="whitespace-nowrap text-xs text-[#4f5a53]">
                    {job.requested_by_username ?? 'System automation'}
                  </Td>
                  <Td className="whitespace-nowrap text-xs tabular-nums text-[#59635d]">
                    {formatDateTime(job.queued_at)}
                  </Td>
                  <Td className="whitespace-nowrap font-mono text-xs tabular-nums text-[#59635d]">
                    {formatDuration(job.duration_seconds)}
                  </Td>
                  <Td>
                    <Link
                      to={`/jobs/${job.id}`}
                      onClick={(event) => event.stopPropagation()}
                      className="grid h-8 w-8 place-items-center rounded-lg text-[#7b857f] transition-colors hover:bg-[#e9ede7] hover:text-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                      aria-label={`Open deployment for ${job.vm_name}`}
                    >
                      <ChevronRight className="h-4 w-4" aria-hidden />
                    </Link>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>

          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-[#68736d]">
            <span className="inline-flex items-center gap-1.5">
              <Activity className="h-3.5 w-3.5 text-brand-600" aria-hidden />
              Showing {data.items.length} of {data.total} deployment{data.total === 1 ? '' : 's'}
            </span>
            {totalPages > 1 && (
              <div className="flex items-center gap-2">
                <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                  Previous
                </Button>
                <span className="min-w-20 text-center tabular-nums">Page {page} of {totalPages}</span>
                <Button size="sm" variant="secondary" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>
                  Next
                </Button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
