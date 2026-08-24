import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { EmptyState, JobStatusBadge, Spinner } from '@/components/ui/feedback'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import { formatDateTime, formatDuration } from '@/lib/utils'

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardContent className="py-4">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
        <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
        {hint && <p className="mt-0.5 text-xs text-slate-400">{hint}</p>}
      </CardContent>
    </Card>
  )
}

export function DashboardPage() {
  const navigate = useNavigate()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.dashboard(),
    refetchInterval: 30_000,
  })

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    )
  }

  if (isError || !data) {
    return <EmptyState title="Dashboard unavailable" description="Could not load dashboard data." />
  }

  const { stats, recent_jobs: recentJobs, health } = data

  return (
    <div className="space-y-6">
      {/* Primary action */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">Provision a virtual machine</h2>
          <p className="text-xs text-slate-500">
            Guided workflow: infrastructure, compute, storage, OS, network, certificates and applications.
          </p>
        </div>
        <Button onClick={() => navigate('/provisioning/new')}>Provision VM</Button>
      </div>

      {/* Statistics */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard label="VMs this month" value={String(stats.vms_provisioned_this_month)} />
        <StatCard
          label="Success rate"
          value={stats.success_rate_percent != null ? `${stats.success_rate_percent}%` : '—'}
        />
        <StatCard
          label="Avg duration"
          value={formatDuration(stats.average_duration_seconds)}
        />
        <StatCard label="Failed jobs" value={String(stats.failed_jobs)} />
        <StatCard label="Active jobs" value={String(stats.active_jobs)} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        {/* Recent jobs */}
        <div className="xl:col-span-2">
          <Card>
            <CardHeader className="flex items-center justify-between">
              <CardTitle>Recent Jobs</CardTitle>
              <Link to="/jobs" className="text-xs font-medium text-brand-600 hover:underline">
                View all →
              </Link>
            </CardHeader>
            <CardContent className="p-0">
              {recentJobs.length === 0 ? (
                <EmptyState title="No provisioning jobs yet" description="Provision your first VM to see it here." />
              ) : (
                <Table>
                  <thead>
                    <tr>
                      <Th>VM Name</Th>
                      <Th>Requested By</Th>
                      <Th>Started</Th>
                      <Th>Duration</Th>
                      <Th>Status</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentJobs.map((job) => (
                      <Tr
                        key={job.id}
                        clickable
                        onClick={() => (window.location.href = `/jobs/${job.id}`)}
                      >
                        <Td className="font-medium text-slate-800">{job.vm_name}</Td>
                        <Td>{job.requested_by_username ?? '—'}</Td>
                        <Td>{formatDateTime(job.started_at ?? job.queued_at)}</Td>
                        <Td>{formatDuration(job.duration_seconds)}</Td>
                        <Td>
                          <JobStatusBadge status={job.status} />
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Infrastructure health */}
        <Card>
          <CardHeader>
            <CardTitle>Infrastructure Health</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {health.map((component) => (
              <div
                key={component.component}
                className="flex items-start justify-between gap-2 border-b border-slate-100 pb-2 last:border-0 last:pb-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-700">{component.component}</p>
                  {component.detail && (
                    <p className="truncate text-xs text-slate-400">{component.detail}</p>
                  )}
                </div>
                <JobStatusBadge
                  status={
                    component.status === 'healthy'
                      ? 'COMPLETED'
                      : component.status === 'unavailable'
                        ? 'FAILED'
                        : 'QUEUED'
                  }
                />
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
