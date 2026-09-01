import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Plus } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { EmptyState, JobStatusBadge, Spinner } from '@/components/ui/feedback'
import { Input, Select } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import { formatDateTime, formatDuration } from '@/lib/utils'
import type { JobListResponse } from '@/types/api'

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'All statuses' },
  { value: 'QUEUED', label: 'Queued' },
  { value: 'RUNNING', label: 'Running' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'PARTIALLY_COMPLETED', label: 'Partially Completed' },
  { value: 'FAILED', label: 'Failed' },
  { value: 'CANCELLED', label: 'Cancelled' },
]

const PAGE_SIZE = 25

export function JobsListPage() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [vmNameFilter, setVmNameFilter] = useState('')

  const { data, isLoading, isError, refetch } = useQuery({
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

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Operations"
        title="Provisioning job queue"
        description="Inspect active and historical executions, open stage-level diagnostics, and retry recoverable failures."
        actions={<Button size="sm" onClick={() => navigate('/provisioning/new')}><Plus className="h-3.5 w-3.5" /> New VM request</Button>}
        meta={<span>{data?.total ?? 0} job(s) match the current scope</span>}
      />

      <div className="console-toolbar">
        <div className="w-56">
          <label className="field-label" htmlFor="jobs-vm-filter">
            VM name contains
          </label>
          <Input
            id="jobs-vm-filter"
            placeholder="e.g. SERVER-PROD"
            value={vmNameFilter}
            onChange={(event) => {
              setPage(1)
              setVmNameFilter(event.target.value)
            }}
          />
        </div>
        <div className="w-52">
          <label className="field-label" htmlFor="jobs-status-filter">
            Status
          </label>
          <Select
            id="jobs-status-filter"
            value={statusFilter}
            onChange={(event) => {
              setPage(1)
              setStatusFilter(event.target.value)
            }}
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </div>
        <Button size="sm" variant="secondary" onClick={() => { setStatusFilter(''); setVmNameFilter(''); setPage(1) }}>
          Clear filters
        </Button>
      </div>

      {isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Spinner />
        </div>
      ) : isError ? (
        <EmptyState
          title="Job queue unavailable"
          description="The provisioning job API could not be reached."
          action={<Button size="sm" variant="secondary" onClick={() => void refetch()}>Retry</Button>}
        />
      ) : !data || data.items.length === 0 ? (
        <EmptyState title="No jobs found" description="Adjust the filters or provision a new VM." />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>VM Name</Th>
                <Th>Requested By</Th>
                <Th>Queued</Th>
                <Th>Started</Th>
                <Th>Duration</Th>
                <Th>Status</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((job) => (
                <Tr key={job.id} clickable onClick={() => navigate(`/jobs/${job.id}`)}>
                  <Td className="font-medium text-slate-800">{job.vm_name}</Td>
                  <Td>{job.requested_by_username ?? '—'}</Td>
                  <Td>{formatDateTime(job.queued_at)}</Td>
                  <Td>{formatDateTime(job.started_at)}</Td>
                  <Td>{formatDuration(job.duration_seconds)}</Td>
                  <Td>
                    <JobStatusBadge status={job.status} />
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>

          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>
              Showing {data.items.length} of {data.total} job(s)
            </span>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <span>
                Page {page} / {totalPages}
              </span>
              <Button
                size="sm"
                variant="secondary"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
