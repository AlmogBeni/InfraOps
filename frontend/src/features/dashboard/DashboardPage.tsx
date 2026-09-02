import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  Clock3,
  RefreshCw,
  Server,
  ShieldCheck,
  Timer,
  TrendingUp,
  XCircle,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Badge, EmptyState, JobStatusBadge, LoadingState } from '@/components/ui/feedback'
import { PageHeader } from '@/components/ui/page'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, displayValue, formatDateTime, formatDuration, humanizeIdentifier } from '@/lib/utils'
import type { HealthComponent, JobOut } from '@/types/api'

type ServiceState = 'operational' | 'attention' | 'unavailable' | 'unknown'

const SERVICE_STATE_META: Record<
  ServiceState,
  {
    label: string
    tone: 'success' | 'warning' | 'danger' | 'neutral'
    icon: typeof CheckCircle2
    dot: string
  }
> = {
  operational: {
    label: 'Operational',
    tone: 'success',
    icon: CheckCircle2,
    dot: 'bg-emerald-500',
  },
  attention: {
    label: 'Needs attention',
    tone: 'warning',
    icon: AlertTriangle,
    dot: 'bg-amber-500',
  },
  unavailable: {
    label: 'Unavailable',
    tone: 'danger',
    icon: XCircle,
    dot: 'bg-red-500',
  },
  unknown: {
    label: 'Status unknown',
    tone: 'neutral',
    icon: AlertTriangle,
    dot: 'bg-stone-400',
  },
}

function serviceState(status: string): ServiceState {
  switch (status.trim().toLowerCase()) {
    case 'healthy':
    case 'ready':
    case 'connected':
      return 'operational'
    case 'degraded':
    case 'warning':
    case 'disabled':
      return 'attention'
    case 'unavailable':
    case 'error':
    case 'failed':
      return 'unavailable'
    default:
      return 'unknown'
  }
}

function serviceName(component: string): string {
  const normalized = component.trim().toLowerCase()
  if (normalized.startsWith('secrets provider')) return 'Credential service'
  if (normalized === 'application repository') return 'Application catalog'
  if (normalized === 'certificate repository') return 'Certificate catalog'
  if (normalized.startsWith('vcenter ')) return `vCenter ${component.trim().slice(8).trim()}`
  return humanizeIdentifier(component)
}

function serviceDescription(component: string, state: ServiceState): string {
  const normalized = component.trim().toLowerCase()
  if (normalized.startsWith('secrets provider')) {
    return state === 'operational'
      ? 'Credential lookups are available.'
      : 'Credential lookups require attention.'
  }
  if (normalized === 'application repository') {
    return state === 'operational'
      ? 'Application inventory is available.'
      : 'Application inventory requires attention.'
  }
  if (normalized === 'certificate repository') {
    return state === 'operational'
      ? 'Certificate inventory is available.'
      : 'Certificate inventory requires attention.'
  }
  if (normalized.startsWith('vcenter ')) {
    if (state === 'operational') return 'Inventory connection is available.'
    if (state === 'unavailable') return 'Inventory connection is unavailable.'
    return 'Inventory connection requires attention.'
  }
  return state === 'operational'
    ? 'Service is available.'
    : 'Service requires attention.'
}

function jobActivity(job: JobOut): string {
  switch (job.status) {
    case 'QUEUED':
      return 'Waiting to start'
    case 'RUNNING':
      return job.current_stage ? humanizeIdentifier(job.current_stage) : 'Deployment in progress'
    case 'COMPLETED':
      return 'Deployment completed'
    case 'PARTIALLY_COMPLETED':
      return 'Completed with follow-up required'
    case 'FAILED':
      return 'Operator review required'
    case 'CANCELLED':
      return 'Stopped by operator'
  }
}

function jobDuration(job: JobOut): string {
  if (job.duration_seconds != null) return formatDuration(job.duration_seconds)
  if (job.status === 'RUNNING') return `${Math.max(0, Math.min(100, job.progress))}% complete`
  if (job.status === 'QUEUED') return 'Waiting'
  return 'Not recorded'
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string
  value: ReactNode
  detail: string
  icon: typeof Activity
  tone?: 'neutral' | 'success' | 'warning' | 'danger'
}) {
  const iconClasses = {
    neutral: 'bg-[#eef3ef] text-brand-700',
    success: 'bg-emerald-50 text-emerald-700',
    warning: 'bg-amber-50 text-amber-700',
    danger: 'bg-red-50 text-red-700',
  }

  return (
    <div className="dashboard-metric-card rounded-xl border border-[#d8ddd7] bg-white p-4 shadow-[0_1px_2px_rgba(23,32,28,0.04)]">
      <div className="flex items-start justify-between gap-4">
        <dt className="text-[11px] font-semibold text-[#68736d]">{label}</dt>
        <span className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-lg', iconClasses[tone])}>
          <Icon className="h-4 w-4" aria-hidden />
        </span>
      </div>
      <dd className="mt-3 text-2xl font-semibold tabular-nums tracking-[-0.035em] text-[#17201c]">{value}</dd>
      <dd className="mt-1.5 text-[11px] leading-4 text-[#7a847e]">{detail}</dd>
    </div>
  )
}

function ServiceStatus({ component }: { component: HealthComponent }) {
  const state = serviceState(component.status)
  const meta = SERVICE_STATE_META[state]
  const Icon = meta.icon
  const name = serviceName(component.component)

  return (
    <li className="border-b border-[#e5e8e4] px-5 py-4 last:border-b-0">
      <div className="flex items-start gap-3">
        <span className="relative mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-[#dfe4de] bg-[#f7f8f5]">
          <Icon className="h-4 w-4 text-[#58635c]" aria-hidden />
          <span className={cn('absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-white', meta.dot, state === 'operational' && 'dashboard-live-dot')} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="truncate text-sm font-semibold text-[#253029]">{name}</h3>
            <Badge tone={meta.tone}>{meta.label}</Badge>
          </div>
          <p className="mt-1 text-xs leading-5 text-[#758079]">{serviceDescription(component.component, state)}</p>
        </div>
      </div>
    </li>
  )
}

function RecentDeployments({ jobs, canProvision }: { jobs: JobOut[]; canProvision: boolean }) {
  if (jobs.length === 0) {
    return (
      <div className="p-5">
        <EmptyState
          title="No deployments have been submitted yet"
          description="Your latest virtual machine deployments will appear here as soon as work begins."
          action={canProvision ? (
            <Link
              to="/provisioning/new"
              className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#153f34] bg-[#174f40] px-4 text-xs font-semibold text-white shadow-sm hover:bg-[#123f34] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"
            >
              Create a virtual machine <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : undefined}
        />
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-left">
        <caption className="sr-only">Eight most recent virtual machine deployments</caption>
        <thead>
          <tr className="border-b border-[#dfe4de] bg-[#f7f8f5] text-[10px] font-bold uppercase tracking-[0.11em] text-[#727c76]">
            <th scope="col" className="px-5 py-3">Virtual machine</th>
            <th scope="col" className="px-4 py-3">Requested by</th>
            <th scope="col" className="px-4 py-3">Current activity</th>
            <th scope="col" className="px-4 py-3">Requested</th>
            <th scope="col" className="px-4 py-3">Elapsed</th>
            <th scope="col" className="px-4 py-3">Status</th>
            <th scope="col" className="w-12 px-4 py-3"><span className="sr-only">Open deployment</span></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id} className="group border-b border-[#e5e8e4] last:border-b-0 hover:bg-[#f8f9f6]">
              <th scope="row" className="px-5 py-4 text-sm font-semibold text-[#202923]">
                <Link
                  to={`/jobs/${job.id}`}
                  className="rounded-sm outline-none hover:text-brand-700 hover:underline focus-visible:ring-2 focus-visible:ring-brand-500"
                >
                  {displayValue(job.vm_name, 'Unnamed virtual machine')}
                </Link>
              </th>
              <td className="px-4 py-4 text-xs text-[#59635d]">
                {displayValue(job.requested_by_username, 'Automated request')}
              </td>
              <td className="max-w-56 px-4 py-4">
                <span className="block truncate text-xs font-medium text-[#465149]">{jobActivity(job)}</span>
                {job.status === 'RUNNING' && (
                  <span className="mt-2 block h-1.5 w-full max-w-32 overflow-hidden rounded-full bg-[#e3e7e2]" aria-hidden>
                    <span className="dashboard-progress-fill block h-full rounded-full bg-[#e56b3f] transition-[width] duration-700 ease-out" style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
                  </span>
                )}
              </td>
              <td className="whitespace-nowrap px-4 py-4 text-[11px] tabular-nums text-[#68736d]">
                {job.queued_at ? <time dateTime={job.queued_at}>{formatDateTime(job.queued_at)}</time> : 'Not recorded'}
              </td>
              <td className="whitespace-nowrap px-4 py-4 text-xs font-medium tabular-nums text-[#465149]">
                {jobDuration(job)}
              </td>
              <td className="px-4 py-4"><JobStatusBadge status={job.status} /></td>
              <td className="px-4 py-4 text-right">
                <Link
                  to={`/jobs/${job.id}`}
                  aria-label={`Open deployment for ${displayValue(job.vm_name, 'virtual machine')}`}
                  className="inline-grid h-8 w-8 place-items-center rounded-lg text-[#8a938d] transition-colors hover:bg-white hover:text-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                >
                  <ChevronRight className="h-4 w-4" aria-hidden />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DashboardLoading() {
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Operations"
        title="Dashboard"
        description="Deployment activity and infrastructure service health."
      />
      <LoadingState
        title="Loading dashboard"
        description="Retrieving deployment and service status."
      />
    </div>
  )
}

export function DashboardPage() {
  const navigate = useNavigate()
  const { hasPermission, hasRole } = useAuth()
  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.dashboard(),
    refetchInterval: 30_000,
  })

  if (dashboard.isLoading) return <DashboardLoading />

  if (dashboard.isError || !dashboard.data) {
    return (
      <div className="space-y-6">
        <PageHeader
          eyebrow="Operations"
          title="Dashboard"
          description="Deployment activity and infrastructure service health."
        />
        <EmptyState
          title="Operations data could not be loaded"
          description="Dashboard data is currently unavailable. Try again."
          action={(
            <Button variant="secondary" loading={dashboard.isFetching} onClick={() => void dashboard.refetch()}>
              <RefreshCw className="h-4 w-4" aria-hidden /> Try again
            </Button>
          )}
        />
      </div>
    )
  }

  const { stats, recent_jobs: recentJobs, health } = dashboard.data
  const canProvision = hasPermission('provisioning.submit')
  const serviceStates = health.map((component) => serviceState(component.status))
  const operationalServices = serviceStates.filter((state) => state === 'operational').length
  const unavailableServices = serviceStates.filter((state) => state === 'unavailable').length
  const attentionServices = serviceStates.filter((state) => state !== 'operational').length
  const allOperational = health.length > 0 && attentionServices === 0
  const serviceSummary = health.length === 0
    ? 'No service checks reported'
    : allOperational
      ? 'All monitored services operational'
      : unavailableServices > 0
        ? `${unavailableServices} service${unavailableServices === 1 ? '' : 's'} unavailable`
        : `${attentionServices} service${attentionServices === 1 ? '' : 's'} require attention`
  const deliveredLabel = new Intl.NumberFormat().format(stats.vms_provisioned_this_month)
  const failedLabel = new Intl.NumberFormat().format(stats.failed_jobs)
  const refreshedAt = dashboard.dataUpdatedAt
    ? formatDateTime(new Date(dashboard.dataUpdatedAt).toISOString())
    : 'Not available'

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Operations"
        title="Dashboard"
        description="Deployment activity and infrastructure service health."
        actions={(
          <>
            <Button
              size="sm"
              variant="secondary"
              disabled={dashboard.isFetching}
              onClick={() => void dashboard.refetch()}
            >
              <RefreshCw className={cn('h-3.5 w-3.5', dashboard.isFetching && 'ui-spinner')} aria-hidden /> Refresh
            </Button>
            {canProvision && (
              <Button size="sm" onClick={() => navigate('/provisioning/new')}>
                <Server className="h-3.5 w-3.5" aria-hidden /> Create VM
              </Button>
            )}
          </>
        )}
        meta={(
          <>
            <span className="inline-flex items-center gap-2">
              <span className="dashboard-live-dot h-2 w-2 rounded-full bg-emerald-500" aria-hidden /> Auto-refresh: 30 seconds
            </span>
            <span role="status" aria-live="polite">
              {dashboard.isFetching ? 'Refreshing…' : `Last updated ${refreshedAt}`}
            </span>
          </>
        )}
      />

      <section className="dashboard-panel-enter flex flex-col gap-4 rounded-xl border border-[#d2dad3] bg-white px-4 py-4 shadow-[0_1px_2px_rgba(23,32,28,0.04)] sm:flex-row sm:items-center sm:justify-between" aria-labelledby="service-summary-title">
        <div className="flex min-w-0 items-start gap-3">
          <span className={cn(
            'grid h-9 w-9 shrink-0 place-items-center rounded-lg',
            allOperational
              ? 'bg-emerald-50 text-emerald-700'
              : unavailableServices > 0
                ? 'bg-red-50 text-red-700'
                : 'bg-amber-50 text-amber-800',
          )}>
            {allOperational
              ? <ShieldCheck className="h-[18px] w-[18px]" aria-hidden />
              : <AlertTriangle className="h-[18px] w-[18px]" aria-hidden />}
          </span>
          <div className="min-w-0">
            <h2 id="service-summary-title" className="text-sm font-semibold text-[#202923]">{serviceSummary}</h2>
            <p className="mt-1 text-xs text-[#68736d]">
              {health.length > 0 ? `${operationalServices} of ${health.length} checks passing.` : 'Refresh to request current service status.'}
            </p>
          </div>
        </div>
        {hasRole('administrator') && (
          <Link to="/admin/vmware" className="shrink-0 text-xs font-semibold text-brand-700 hover:underline">
            Review infrastructure connections
          </Link>
        )}
      </section>

      <section aria-labelledby="dashboard-metrics-title" className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 id="dashboard-metrics-title" className="text-sm font-semibold text-[#202923]">Current metrics</h2>
          <Link to="/jobs" className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-700 hover:underline">
            View all deployments <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </Link>
        </div>
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard label="Active deployments" value={stats.active_jobs} detail="Queued or running" icon={Activity} />
          <MetricCard label="VMs provisioned" value={deliveredLabel} detail="This month" icon={Server} />
          <MetricCard
            label="Success rate"
            value={stats.success_rate_percent != null ? `${stats.success_rate_percent}%` : 'No data'}
            detail="Completed deployments"
            icon={TrendingUp}
            tone={stats.success_rate_percent == null
              ? 'neutral'
              : stats.success_rate_percent >= 95
                ? 'success'
                : stats.success_rate_percent >= 80
                  ? 'warning'
                  : 'danger'}
          />
          <MetricCard
            label="Average duration"
            value={stats.average_duration_seconds != null ? formatDuration(stats.average_duration_seconds) : 'No data'}
            detail="Completed deployments"
            icon={Timer}
          />
          <MetricCard
            label="Failed deployments"
            value={failedLabel}
            detail="This month"
            icon={XCircle}
            tone={stats.failed_jobs === 0 ? 'neutral' : 'danger'}
          />
        </dl>
      </section>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(330px,0.75fr)]">
        <section
          aria-labelledby="recent-deployments-title"
          className="dashboard-panel-enter overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)] [animation-delay:140ms]"
        >
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#e2e6e1] px-5 py-4">
            <div>
              <h2 id="recent-deployments-title" className="text-base font-semibold tracking-[-0.02em] text-[#202923]">Recent deployments</h2>
              <p className="mt-1 text-xs leading-5 text-[#758079]">Latest VM deployment jobs.</p>
            </div>
            <Link to="/jobs" className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-brand-700 hover:bg-brand-50">
              View queue <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          </div>
          <RecentDeployments jobs={recentJobs} canProvision={canProvision} />
        </section>

        <section
          aria-labelledby="service-status-title"
          className="dashboard-panel-enter overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)] [animation-delay:210ms]"
        >
          <div className="border-b border-[#e2e6e1] bg-[#f8f9f6] px-5 py-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="service-status-title" className="text-base font-semibold tracking-[-0.02em] text-[#202923]">System status</h2>
                <p className="mt-1 text-xs leading-5 text-[#758079]">Current dependency checks.</p>
              </div>
              <Badge tone={allOperational ? 'success' : unavailableServices > 0 ? 'danger' : attentionServices > 0 ? 'warning' : 'neutral'}>
                {allOperational ? 'Operational' : attentionServices > 0 ? `${attentionServices} need attention` : 'No checks'}
              </Badge>
            </div>
          </div>

          {health.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title="No service checks were reported"
                description="Refresh the dashboard to request current status."
              />
            </div>
          ) : (
            <ul aria-label="Control-plane service checks">
              {health.map((component, index) => (
                <ServiceStatus key={`${component.component}-${index}`} component={component} />
              ))}
            </ul>
          )}

          <div className="flex items-center gap-2 border-t border-[#e2e6e1] bg-[#f8f9f6] px-5 py-3 text-[11px] leading-5 text-[#68736d]">
            <Clock3 className="h-3.5 w-3.5 shrink-0 text-brand-700" aria-hidden />
            Service checks refresh every 30 seconds.
          </div>
        </section>
      </div>
    </div>
  )
}
