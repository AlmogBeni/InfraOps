import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowRight, RefreshCw, Server } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { EmptyState, JobStatusBadge, Spinner } from '@/components/ui/feedback'
import { ConsolePanel, DataPoint, PageHeader, PanelHeader } from '@/components/ui/page'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import { cn, formatDateTime, formatDuration } from '@/lib/utils'

export function DashboardPage() {
  const navigate = useNavigate()
  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.dashboard(),
    refetchInterval: 30_000,
  })

  if (dashboard.isLoading) {
    return <div className="flex h-64 items-center justify-center"><Spinner /></div>
  }

  if (dashboard.isError || !dashboard.data) {
    return (
      <EmptyState
        title="Operations data unavailable"
        description="The dashboard API did not return a usable response."
        action={<Button size="sm" variant="secondary" onClick={() => void dashboard.refetch()}>Retry</Button>}
      />
    )
  }

  const { stats, recent_jobs: recentJobs, health } = dashboard.data
  const unhealthy = health.filter((component) => component.status !== 'healthy').length

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Operations"
        title="Infrastructure operations overview"
        description="Current provisioning throughput, execution state, and control-plane health in one operational view."
        actions={(
          <>
            <Button size="sm" variant="secondary" onClick={() => void dashboard.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" /> Refresh
            </Button>
            <Button size="sm" onClick={() => navigate('/provisioning/new')}>
              <Server className="h-3.5 w-3.5" /> Provision VM
            </Button>
          </>
        )}
        meta={(
          <>
            <span className="inline-flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Auto-refresh: 30 seconds</span>
            <span>{stats.active_jobs} active execution(s)</span>
          </>
        )}
      />

      <ConsolePanel>
        <div className="grid divide-y divide-slate-200 sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-5">
          <DataPoint label="Provisioned this month" value={stats.vms_provisioned_this_month} detail="Completed VM requests" />
          <DataPoint label="Success rate" value={stats.success_rate_percent != null ? `${stats.success_rate_percent}%` : '—'} detail="Completed versus failed" />
          <DataPoint label="Mean execution" value={formatDuration(stats.average_duration_seconds)} detail="End-to-end duration" />
          <DataPoint label="Failed jobs" value={stats.failed_jobs} detail="Requires operator review" />
          <DataPoint label="Active jobs" value={stats.active_jobs} detail="Queued or running" />
        </div>
      </ConsolePanel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(300px,0.8fr)]">
        <ConsolePanel>
          <PanelHeader
            title="Recent provisioning jobs"
            description="Latest activity across all operators"
            actions={<Link to="/jobs" className="inline-flex items-center gap-1 text-xs font-semibold text-brand-700 hover:underline">Open job queue <ArrowRight className="h-3 w-3" /></Link>}
          />
          {recentJobs.length === 0 ? (
            <EmptyState title="No provisioning activity" description="Completed and active jobs will appear here." />
          ) : (
            <Table className="border-0">
              <thead><tr><Th>Virtual machine</Th><Th>Requested by</Th><Th>Started</Th><Th>Duration</Th><Th>Status</Th></tr></thead>
              <tbody>
                {recentJobs.map((job) => (
                  <Tr key={job.id} clickable onClick={() => navigate(`/jobs/${job.id}`)}>
                    <Td><p className="font-semibold text-slate-900">{job.vm_name}</p><p className="font-mono text-[10px] text-slate-400">{job.id.slice(0, 8)}</p></Td>
                    <Td>{job.requested_by_username ?? '—'}</Td>
                    <Td className="whitespace-nowrap text-xs">{formatDateTime(job.started_at ?? job.queued_at)}</Td>
                    <Td className="font-mono text-xs">{formatDuration(job.duration_seconds)}</Td>
                    <Td><JobStatusBadge status={job.status} /></Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </ConsolePanel>

        <ConsolePanel>
          <PanelHeader title="Control-plane health" description={`${unhealthy} component(s) require attention`} />
          <div className="divide-y divide-slate-200">
            {health.map((component) => {
              const healthy = component.status === 'healthy'
              const unavailable = component.status === 'unavailable'
              return (
                <div key={component.component} className="flex items-start gap-3 px-4 py-3">
                  <span className={cn('mt-1.5 h-2 w-2 shrink-0 rounded-full', healthy ? 'bg-emerald-500' : unavailable ? 'bg-red-500' : 'bg-amber-500')} />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold text-slate-800">{component.component}</p>
                    <p className="mt-0.5 truncate text-[11px] text-slate-500">{component.detail || 'No additional detail'}</p>
                  </div>
                  <span className="text-[9px] font-bold uppercase tracking-wide text-slate-500">{component.status}</span>
                </div>
              )
            })}
          </div>
          <div className="flex items-center gap-2 border-t border-slate-200 bg-slate-950 px-4 py-3 text-[11px] text-slate-400">
            <Activity className="h-3.5 w-3.5 text-brand-500" aria-hidden /> Health state is reported by backend readiness probes.
          </div>
        </ConsolePanel>
      </div>
    </div>
  )
}
