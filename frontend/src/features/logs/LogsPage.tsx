import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Bug,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  RefreshCw,
  Search,
  Siren,
} from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { Input, Select } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { api } from '@/lib/api'
import { displayValue, formatDateTime, humanizeIdentifier } from '@/lib/utils'
import type { LogEventOut, LogSeverity } from '@/types/api'

const SEVERITY_META: Record<LogSeverity, { label: string; tone: Parameters<typeof Badge>[0]['tone']; icon: typeof CircleCheck }> = {
  INFO: { label: 'Info', tone: 'info', icon: CircleCheck },
  WARNING: { label: 'Warning', tone: 'warning', icon: AlertTriangle },
  ERROR: { label: 'Error', tone: 'danger', icon: CircleAlert },
  CRITICAL: { label: 'Critical', tone: 'danger', icon: Siren },
  DEBUG: { label: 'Debug', tone: 'neutral', icon: Bug },
}

interface Filters {
  search: string
  severity: string
  component: string
  datacenter: string
  since: string
  until: string
}

const EMPTY_FILTERS: Filters = { search: '', severity: '', component: '', datacenter: '', since: '', until: '' }

const COMPONENT_OPTIONS = [
  ['provisioning.validate_request', 'Request validation'],
  ['provisioning.connect_vcenter', 'vCenter connection'],
  ['provisioning.validate_infrastructure', 'Infrastructure validation'],
  ['provisioning.clone_vm', 'Create virtual machine'],
  ['provisioning.configure_hardware', 'Hardware configuration'],
  ['provisioning.attach_network_adapter', 'Network adapter'],
  ['provisioning.power_on', 'Power on'],
  ['provisioning.wait_for_guest_os', 'Guest operating system readiness'],
  ['provisioning.wait_for_tools', 'VMware Tools readiness'],
  ['provisioning.configure_guest_network', 'Guest network configuration'],
  ['provisioning.validate_network', 'Network validation'],
  ['provisioning.configure_hostname', 'Guest hostname'],
  ['provisioning.join_domain', 'Domain join'],
  ['provisioning.reboot_guest', 'Guest restart'],
  ['provisioning.wait_guest_ready', 'Guest availability'],
  ['provisioning.install_root_certificates', 'Root certificate installation'],
  ['provisioning.install_intermediate_certificates', 'Intermediate certificate installation'],
  ['provisioning.validate_certificates', 'Certificate validation'],
  ['provisioning.resolve_dependencies', 'Application dependency resolution'],
  ['provisioning.install_applications', 'Application installation'],
  ['provisioning.validate_applications', 'Application validation'],
  ['provisioning.final_validation', 'Final validation'],
] as const

const COMPONENT_LABELS = new Map<string, string>(COMPONENT_OPTIONS)

function componentLabel(value: string): string {
  return COMPONENT_LABELS.get(value) ?? humanizeIdentifier(value.replace(/^provisioning[._-]?/, ''))
}

function StructuredDetails({ details }: { details: Record<string, unknown> }) {
  const entries = Object.entries(details).filter(([, value]) => value !== null && value !== undefined && value !== '')
  if (entries.length === 0) return <p className="text-xs text-[#758079]">No additional details were recorded.</p>
  return (
    <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">{humanizeIdentifier(key)}</dt>
          <dd className="mt-1 break-words text-xs leading-5 text-[#364039]">
            {typeof value === 'object' ? (
              <details className="rounded-lg border border-[#d8ddd7] bg-white px-3 py-2">
                <summary className="cursor-pointer font-semibold text-brand-700">View technical context</summary>
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[#17201c] p-3 font-mono text-[10px] leading-5 text-white/75">{JSON.stringify(value, null, 2)}</pre>
              </details>
            ) : displayValue(value)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function LogRow({ event }: { event: LogEventOut }) {
  const [expanded, setExpanded] = useState(false)
  const meta = SEVERITY_META[event.severity] ?? SEVERITY_META.INFO
  const Icon = meta.icon
  const hasDetails = Object.keys(event.details).length > 0 || Boolean(event.datacenter_name || event.job_id)
  return (
    <article className="group/log border-b border-[#e6e9e5] last:border-b-0">
      <button
        type="button"
        className="grid w-full gap-3 px-5 py-4 text-left transition-[background-color,padding] duration-200 hover:bg-[#f2f7f2] md:grid-cols-[160px_105px_150px_minmax(0,1fr)_180px_24px] md:items-center"
        onClick={() => hasDetails && setExpanded((value) => !value)}
        disabled={!hasDetails}
        aria-expanded={hasDetails ? expanded : undefined}
      >
        <time className="text-[11px] tabular-nums text-[#68736d]">{formatDateTime(event.timestamp)}</time>
        <Badge tone={meta.tone}><Icon className="h-3 w-3" /> {meta.label}</Badge>
        <span className="truncate text-xs font-medium text-[#4c5750]">{componentLabel(event.component)}</span>
        <span className="min-w-0 text-sm leading-5 text-[#222b26]">{displayValue(event.message, 'No message was recorded.')}</span>
        <span className="truncate text-xs text-[#68736d]">{event.resource_name ?? event.datacenter_name ?? 'Platform'}</span>
        {hasDetails && (expanded ? <ChevronDown className="h-4 w-4 text-[#87908a]" /> : <ChevronRight className="h-4 w-4 text-[#87908a]" />)}
      </button>
      {expanded && (
        <div className="animate-panel-reveal border-t border-[#dfe6df] bg-[radial-gradient(circle_at_100%_0%,rgba(216,240,106,0.1),transparent_42%),#f8faf7] px-5 py-4 md:pl-[296px]">
          <div className="mb-4 flex flex-wrap gap-4 text-xs text-[#68736d]">
            {event.datacenter_name && <span><strong className="text-[#3c4740]">Datacenter:</strong> {event.datacenter_name}</span>}
            {event.job_id && <Link className="font-semibold text-brand-700 hover:underline" to={`/jobs/${event.job_id}`}>Open related deployment</Link>}
          </div>
          <StructuredDetails details={event.details} />
        </div>
      )}
    </article>
  )
}

export function LogsPage() {
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [page, setPage] = useState(1)
  const logs = useQuery({
    queryKey: ['logs', page, filters],
    queryFn: () => api.logs({ page, page_size: 50, ...filters }),
    refetchInterval: 30_000,
  })
  const totalPages = logs.data ? Math.max(1, Math.ceil(logs.data.total / logs.data.page_size)) : 1

  function applyFilters(event: React.FormEvent) {
    event.preventDefault()
    setPage(1)
    setFilters(draft)
  }

  function clearFilters() {
    setDraft(EMPTY_FILTERS)
    setFilters(EMPTY_FILTERS)
    setPage(1)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Operations intelligence"
        title="Deployment logs"
        description="Scan structured execution messages by time, severity, source, resource, and datacenter. Open a row for supporting context."
        actions={<Button variant="secondary" size="sm" onClick={() => void logs.refetch()}><RefreshCw className="h-3.5 w-3.5" /> Refresh</Button>}
        meta={<span>{logs.data?.total ?? 0} log events · refreshes every 30 seconds</span>}
      />

      <form className="animate-panel-reveal rounded-2xl border border-[#d4ddd5] bg-[radial-gradient(circle_at_100%_0%,rgba(216,240,106,0.12),transparent_34%),linear-gradient(135deg,#ffffff_0%,#f6faf6_100%)] p-4 shadow-[var(--ui-shadow)]" onSubmit={applyFilters}>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(240px,1.3fr)_150px_170px_180px_170px_170px_auto] xl:items-end">
          <div className="relative">
            <label className="field-label" htmlFor="log-search">Search messages</label>
            <Search className="pointer-events-none absolute bottom-3 left-3 h-4 w-4 text-[#87908a]" />
            <Input id="log-search" className="pl-9" placeholder="Message or resource" value={draft.search} onChange={(event) => setDraft({ ...draft, search: event.target.value })} />
          </div>
          <div>
            <label className="field-label" htmlFor="log-severity">Severity</label>
            <Select id="log-severity" value={draft.severity} onChange={(event) => setDraft({ ...draft, severity: event.target.value })}>
              <option value="">All severities</option>
              {Object.entries(SEVERITY_META).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}
            </Select>
          </div>
          <div>
            <label className="field-label" htmlFor="log-component">Component</label>
            <Select id="log-component" value={draft.component} onChange={(event) => setDraft({ ...draft, component: event.target.value })}>
              <option value="">All components</option>
              {COMPONENT_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
          </div>
          <div>
            <label className="field-label" htmlFor="log-datacenter">Datacenter</label>
            <Input id="log-datacenter" placeholder="Datacenter name" value={draft.datacenter} onChange={(event) => setDraft({ ...draft, datacenter: event.target.value })} />
          </div>
          <div>
            <label className="field-label" htmlFor="log-since">From</label>
            <Input id="log-since" type="datetime-local" value={draft.since} onChange={(event) => setDraft({ ...draft, since: event.target.value })} />
          </div>
          <div>
            <label className="field-label" htmlFor="log-until">To</label>
            <Input id="log-until" type="datetime-local" value={draft.until} onChange={(event) => setDraft({ ...draft, until: event.target.value })} />
          </div>
          <div className="flex gap-2"><Button type="submit" size="sm">Apply</Button><Button type="button" variant="ghost" size="sm" onClick={clearFilters}>Reset</Button></div>
        </div>
      </form>

      {logs.isLoading ? (
        <LoadingState title="Loading deployment logs" description="Retrieving structured execution messages." />
      ) : logs.isError ? (
        <EmptyState title="Logs could not be loaded" description={logs.error instanceof Error ? logs.error.message : 'Try again in a moment.'} action={<Button variant="secondary" onClick={() => void logs.refetch()}>Try again</Button>} />
      ) : !logs.data || logs.data.items.length === 0 ? (
        <EmptyState title="No log events match these filters" description="Clear the filters or wait for a deployment to produce activity." />
      ) : (
        <section className="overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)]" aria-label="Log events">
          <div className="hidden grid-cols-[160px_105px_150px_minmax(0,1fr)_180px_24px] gap-3 border-b border-[#d9e2da] bg-[linear-gradient(135deg,#f8faf6_0%,#eef5ef_100%)] px-5 py-3 text-[10px] font-bold uppercase tracking-[0.1em] text-[#647068] md:grid">
            <span>Timestamp</span><span>Severity</span><span>Source</span><span>Message</span><span>Resource</span><span />
          </div>
          {logs.data.items.map((event) => <LogRow key={event.id} event={event} />)}
        </section>
      )}

      {logs.data && logs.data.total > logs.data.page_size && (
        <div className="flex items-center justify-between text-xs text-[#68736d]">
          <span>Showing {logs.data.items.length} of {logs.data.total} events</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</Button>
            <span>Page {page} of {totalPages}</span>
            <Button size="sm" variant="secondary" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>Next</Button>
          </div>
        </div>
      )}
    </div>
  )
}
