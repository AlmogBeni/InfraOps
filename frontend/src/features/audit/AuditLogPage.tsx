import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Building2,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Clock3,
  Filter,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  UserRound,
  XCircle,
} from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { Input, Select } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { api } from '@/lib/api'
import { displayValue, formatAction, formatDateTime, humanizeIdentifier } from '@/lib/utils'
import type { AuditEventOut } from '@/types/api'

interface AuditFilters {
  search: string
  username: string
  action: string
  resource_type: string
  result: string
  datacenter: string
  since: string
  until: string
}

export interface AuditDisplayDetail {
  key: string
  label: string
  value: string
}

const EMPTY_FILTERS: AuditFilters = {
  search: '', username: '', action: '', resource_type: '', result: '', datacenter: '', since: '', until: '',
}

const ACTION_OPTIONS = [
  ['AUTH_LOGIN', 'Signed in'],
  ['AUTH_LOGIN_FAILED', 'Sign-in failed'],
  ['AUTH_LOGOUT', 'Signed out'],
  ['PROVISIONING_REQUEST_CREATED', 'Deployment submitted'],
  ['DRY_RUN_PERFORMED', 'Configuration validated'],
  ['IP_CONFLICT_CHECK_PERFORMED', 'IP address checked'],
  ['JOB_STARTED', 'Deployment started'],
  ['JOB_STAGE_SUCCEEDED', 'Deployment stage succeeded'],
  ['JOB_STAGE_FAILED', 'Deployment stage failed'],
  ['JOB_COMPLETED', 'Deployment completed'],
  ['JOB_PARTIALLY_COMPLETED', 'Deployment partially completed'],
  ['JOB_FAILED', 'Deployment failed'],
  ['JOB_CANCELLED', 'Deployment cancelled'],
  ['JOB_STAGE_RETRIED', 'Deployment stage retried'],
  ['VM_CREATED', 'VM created'],
  ['NETWORK_MODIFIED', 'Network modified'],
  ['CERTIFICATE_INSTALLED', 'Certificate installed'],
  ['APPLICATION_INSTALLED', 'Application installed'],
  ['VCENTER_CREATED', 'vCenter connection created'],
  ['VCENTER_UPDATED', 'vCenter connection updated'],
  ['VCENTER_DELETED', 'vCenter connection deleted'],
  ['VCENTER_TESTED', 'vCenter connection tested'],
  ['CERTIFICATE_PACKAGE_CREATED', 'Certificate package created'],
  ['CERTIFICATE_PACKAGE_UPDATED', 'Certificate package updated'],
  ['CERTIFICATE_PACKAGE_DELETED', 'Certificate package deleted'],
  ['CERTIFICATE_CREATED', 'Certificate created'],
  ['CERTIFICATE_UPDATED', 'Certificate updated'],
  ['CERTIFICATE_DELETED', 'Certificate deleted'],
  ['APPLICATION_CREATED', 'Application created'],
  ['APPLICATION_UPDATED', 'Application updated'],
  ['APPLICATION_DELETED', 'Application deleted'],
  ['CREDENTIAL_CREATED', 'Credential reference created'],
  ['CREDENTIAL_DELETED', 'Credential reference deleted'],
  ['SETTINGS_UPDATED', 'Platform settings updated'],
] as const

const HIDDEN_DETAIL_KEY = /(?:^id$|(?:^|_)ids?$|secret|password|passphrase|token|credential|artifact|technical|thumbprint|fingerprint|private.?key|api.?key|payload|request|response|(?:^|_)path$|(?:^|_)uri$|(?:^|_)url$|reference)/i
const OPAQUE_VALUE = /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|(?:vm|host|datastore|network|dvportgroup|domain-c|resgroup|group-v|datacenter)-\d+|urn:[^\s]+)$/i
const PROTECTED_VALUE = /^(?:\*+|\[?redacted\]?|<redacted>)$/i
const HUMANIZED_VALUE_KEYS = new Set(['action', 'fields', 'network_mode', 'provider', 'result', 'source_type', 'stage', 'status'])

function resultTone(result: string | null): 'success' | 'danger' | 'warning' | 'neutral' | 'info' {
  switch (result?.toLowerCase()) {
    case 'success':
    case 'ready':
    case 'clear':
    case 'completed':
      return 'success'
    case 'failure':
    case 'failed':
    case 'blocked':
    case 'conflict':
      return 'danger'
    case 'cancelled':
    case 'partial':
    case 'cancel_requested':
      return 'warning'
    case 'running':
    case 'queued':
    case 'requeued':
      return 'info'
    default:
      return 'neutral'
  }
}

function resultLabel(result: string | null): string {
  if (!result) return 'Recorded'
  if (result.toLowerCase() === 'clear') return 'No conflict'
  return humanizeIdentifier(result)
}

function resultVisual(result: string | null) {
  switch (resultTone(result)) {
    case 'success':
      return { icon: CheckCircle2, iconClass: 'bg-emerald-50 text-emerald-700 ring-emerald-200', railClass: 'bg-emerald-500' }
    case 'danger':
      return { icon: XCircle, iconClass: 'bg-red-50 text-red-700 ring-red-200', railClass: 'bg-red-500' }
    case 'warning':
      return { icon: CircleAlert, iconClass: 'bg-amber-50 text-amber-800 ring-amber-200', railClass: 'bg-amber-400' }
    case 'info':
      return { icon: Activity, iconClass: 'bg-brand-50 text-brand-700 ring-brand-200', railClass: 'bg-brand-500' }
    default:
      return { icon: CircleDot, iconClass: 'bg-[#f0f2ef] text-[#65706a] ring-[#dce1dc]', railClass: 'bg-[#aab3ad]' }
  }
}

function eventActionLabel(event: AuditEventOut): string {
  return displayValue(event.action_label, formatAction(event.action))
}

function formatDetailPrimitive(key: string, value: string | number | boolean): string | null {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null
    if (key === 'duration_seconds') return `${value} second${value === 1 ? '' : 's'}`
    return String(value)
  }
  const trimmed = value.trim()
  if (!trimmed || PROTECTED_VALUE.test(trimmed) || OPAQUE_VALUE.test(trimmed)) return null
  if (HUMANIZED_VALUE_KEYS.has(key) || /^[A-Z][A-Z0-9_.-]*$/.test(trimmed)) return humanizeIdentifier(trimmed)
  return trimmed
}

function formatDetailValue(key: string, value: unknown): string | null {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return formatDetailPrimitive(key, value)
  }
  if (!Array.isArray(value)) return null
  const values = value
    .filter((item): item is string | number | boolean => ['string', 'number', 'boolean'].includes(typeof item))
    .map((item) => formatDetailPrimitive(key, item))
    .filter((item): item is string => Boolean(item))
  if (values.length === 0) return null
  const visible = values.slice(0, 4)
  return `${visible.join(', ')}${values.length > visible.length ? ` (+${values.length - visible.length} more)` : ''}`
}

/** Convert metadata to safe operator-facing facts without raw payloads or identifiers. */
export function safeAuditDetails(details: Record<string, unknown>): AuditDisplayDetail[] {
  return Object.entries(details).flatMap(([key, rawValue]) => {
    if (HIDDEN_DETAIL_KEY.test(key)) return []
    const value = formatDetailValue(key.toLowerCase(), rawValue)
    if (!value) return []
    return [{ key, label: humanizeIdentifier(key), value }]
  })
}

function AuditDetails({ event }: { event: AuditEventOut }) {
  const details = safeAuditDetails(event.details)
  const hasProtectedDetails = Object.keys(event.details).length > details.length
  const visual = resultVisual(event.result)
  const ResultIcon = visual.icon

  return (
    <div className="space-y-5">
      <section className="relative overflow-hidden rounded-2xl border border-[#285f50] bg-[#173f34] p-5 text-white shadow-[0_16px_38px_rgba(23,63,52,0.18)]">
        <span className="pointer-events-none absolute -right-10 -top-20 h-40 w-40 rounded-full bg-[#d8f06a]/10 blur-2xl" aria-hidden />
        <div className="relative flex items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#d8f06a] text-[#173f34]"><ResultIcon className="h-5 w-5" aria-hidden /></span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h3 className="text-base font-semibold tracking-[-0.02em]">{eventActionLabel(event)}</h3>
                <p className="mt-1 text-xs text-white/65">{displayValue(event.resource_name, 'Platform')} · {humanizeIdentifier(event.resource_type)}</p>
              </div>
              <Badge tone={resultTone(event.result)}><ResultIcon className="h-3 w-3" /> {resultLabel(event.result)}</Badge>
            </div>
          </div>
        </div>
        <dl className="relative mt-5 grid gap-3 border-t border-white/10 pt-4 sm:grid-cols-3">
          <div><dt className="text-[9px] font-bold uppercase tracking-[0.11em] text-white/45">Actor</dt><dd className="mt-1 truncate text-xs font-medium">{event.username ?? 'System automation'}</dd></div>
          <div><dt className="text-[9px] font-bold uppercase tracking-[0.11em] text-white/45">Datacenter</dt><dd className="mt-1 truncate text-xs font-medium">{displayValue(event.datacenter_name)}</dd></div>
          <div><dt className="text-[9px] font-bold uppercase tracking-[0.11em] text-white/45">Occurred</dt><dd className="mt-1 text-xs font-medium tabular-nums">{formatDateTime(event.timestamp)}</dd></div>
        </dl>
      </section>

      {(event.detail_text || details.length > 0 || hasProtectedDetails) && (
        <section className="rounded-2xl border border-[#dfe4de] bg-[#fbfcf9] p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-100"><Activity className="h-4 w-4" /></span>
            <div><h3 className="text-sm font-semibold text-[#202923]">What changed</h3><p className="mt-0.5 text-[11px] leading-4 text-[#758079]">A readable summary with protected system context omitted.</p></div>
          </div>
          {event.detail_text && <p className="mt-4 rounded-xl border border-[#e1e6df] bg-white p-3.5 text-xs leading-5 text-[#4f5a53]">{event.detail_text}</p>}
          {details.length > 0 && (
            <dl className="mt-4 grid gap-3 sm:grid-cols-2">
              {details.map((detail) => (
                <div key={detail.key} className="min-w-0 rounded-xl border border-[#e1e6df] bg-white px-3.5 py-3">
                  <dt className="text-[9px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">{detail.label}</dt>
                  <dd className="mt-1.5 break-words text-xs font-medium leading-5 text-[#364039]">{detail.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {hasProtectedDetails && <p className="mt-3 flex items-start gap-2 text-[10px] leading-4 text-[#7b857f]"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-700" />Additional technical identifiers and provider context are retained in the immutable record but are not displayed here.</p>}
        </section>
      )}

      <section className="rounded-2xl border border-[#dfe4de] bg-white p-4 sm:p-5">
        <div className="flex items-center gap-2"><CalendarClock className="h-4 w-4 text-brand-700" /><h3 className="text-sm font-semibold text-[#202923]">Event context</h3></div>
        <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2">
          <div className="rounded-xl bg-[#f7f9f5] px-3.5 py-3"><dt className="text-[10px] text-[#7b857f]">Timestamp</dt><dd className="mt-1 font-medium tabular-nums">{formatDateTime(event.timestamp)}</dd></div>
          <div className="rounded-xl bg-[#f7f9f5] px-3.5 py-3"><dt className="text-[10px] text-[#7b857f]">Resource type</dt><dd className="mt-1 font-medium">{humanizeIdentifier(event.resource_type)}</dd></div>
          {event.datacenter_name && <div className="rounded-xl bg-[#f7f9f5] px-3.5 py-3"><dt className="text-[10px] text-[#7b857f]">Datacenter</dt><dd className="mt-1 font-medium">{event.datacenter_name}</dd></div>}
          {event.source_ip && <div className="rounded-xl bg-[#f7f9f5] px-3.5 py-3"><dt className="text-[10px] text-[#7b857f]">Source address</dt><dd className="mt-1 font-medium tabular-nums">{event.source_ip}</dd></div>}
          {event.job_id && <div className="rounded-xl bg-[#f7f9f5] px-3.5 py-3"><dt className="text-[10px] text-[#7b857f]">Related deployment</dt><dd className="mt-1"><Link to={'/jobs/' + event.job_id} className="font-semibold text-brand-700 transition-colors hover:text-brand-900 hover:underline">Open deployment details</Link></dd></div>}
        </dl>
      </section>
    </div>
  )
}

export function AuditLogPage() {
  const [page, setPage] = useState(1)
  const [draft, setDraft] = useState<AuditFilters>(EMPTY_FILTERS)
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_FILTERS)
  const [selected, setSelected] = useState<AuditEventOut | null>(null)
  const audit = useQuery({
    queryKey: ['audit', page, filters],
    queryFn: () => api.audit({ page, page_size: 50, ...filters }),
    refetchInterval: 30_000,
  })
  const totalPages = audit.data ? Math.max(1, Math.ceil(audit.data.total / audit.data.page_size)) : 1
  const activeFilterCount = Object.values(filters).filter(Boolean).length

  function apply(event: FormEvent) {
    event.preventDefault()
    setPage(1)
    setFilters(draft)
  }

  function reset() {
    setDraft(EMPTY_FILTERS)
    setFilters(EMPTY_FILTERS)
    setPage(1)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Governance"
        title="Audit trail"
        description="Understand who acted, what changed, which resource was affected, when it happened, and whether it succeeded."
        actions={<Button variant="secondary" size="sm" onClick={() => void audit.refetch()}><RefreshCw className={`h-3.5 w-3.5 ${audit.isFetching ? 'animate-spin' : ''}`} /> Refresh</Button>}
        meta={<><span>{audit.data?.total ?? 0} event{audit.data?.total === 1 ? '' : 's'}</span><span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5 text-brand-700" /> Immutable activity history</span></>}
      />

      <form className="animate-panel-reveal overflow-hidden rounded-2xl border border-[#cedbd2] bg-white shadow-[var(--ui-shadow)]" onSubmit={apply}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#dce5dd] bg-[linear-gradient(135deg,#f7faf5_0%,#edf5ee_100%)] px-4 py-3.5 sm:px-5">
          <div className="flex items-center gap-3">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-700 text-white shadow-sm"><SlidersHorizontal className="h-4 w-4" /></span>
            <div><h2 className="text-xs font-semibold text-[#26312b]">Find activity</h2><p className="mt-0.5 text-[10px] text-[#748078]">Narrow the history by actor, resource, outcome, or time.</p></div>
          </div>
          {activeFilterCount > 0 && <Badge tone="success">{activeFilterCount} active filter{activeFilterCount === 1 ? '' : 's'}</Badge>}
        </div>
        <div className="grid gap-3 p-4 sm:p-5 md:grid-cols-2 xl:grid-cols-4">
          <div className="relative md:col-span-2">
            <label className="field-label" htmlFor="audit-search">Search</label>
            <Search className="pointer-events-none absolute bottom-3 left-3 h-4 w-4 text-[#87908a]" />
            <Input id="audit-search" className="pl-9" placeholder="Action or resource" value={draft.search} onChange={(event) => setDraft({ ...draft, search: event.target.value })} />
          </div>
          <div><label className="field-label" htmlFor="audit-user">Actor</label><Input id="audit-user" placeholder="Username" value={draft.username} onChange={(event) => setDraft({ ...draft, username: event.target.value })} /></div>
          <div><label className="field-label" htmlFor="audit-datacenter">Datacenter</label><Input id="audit-datacenter" placeholder="Datacenter name" value={draft.datacenter} onChange={(event) => setDraft({ ...draft, datacenter: event.target.value })} /></div>
          <div>
            <label className="field-label" htmlFor="audit-action">Action</label>
            <Select id="audit-action" value={draft.action} onChange={(event) => setDraft({ ...draft, action: event.target.value })}>
              <option value="">All actions</option>
              {ACTION_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
          </div>
          <div>
            <label className="field-label" htmlFor="audit-resource-type">Resource type</label>
            <Select id="audit-resource-type" value={draft.resource_type} onChange={(event) => setDraft({ ...draft, resource_type: event.target.value })}>
              <option value="">All resources</option>
              <option value="virtual_machine">Virtual machine</option>
              <option value="provisioning_job">Deployment job</option>
              <option value="provisioning_request">Deployment request</option>
              <option value="provisioning_stage">Deployment stage</option>
              <option value="vcenter">vCenter</option>
              <option value="application">Application</option>
              <option value="certificate">Certificate</option>
              <option value="certificate_package">Certificate package</option>
              <option value="credential_reference">Credential reference</option>
              <option value="platform_settings">Platform settings</option>
              <option value="ip_address">IP address</option>
              <option value="user">User session</option>
            </Select>
          </div>
          <div>
            <label className="field-label" htmlFor="audit-result">Result</label>
            <Select id="audit-result" value={draft.result} onChange={(event) => setDraft({ ...draft, result: event.target.value })}>
              <option value="">All results</option>
              <option value="success">Successful</option>
              <option value="failure">Failed</option>
              <option value="running">Running</option>
              <option value="queued">Queued</option>
              <option value="requeued">Requeued</option>
              <option value="cancel_requested">Cancellation requested</option>
              <option value="blocked">Blocked</option>
              <option value="cancelled">Cancelled</option>
              <option value="ready">Ready</option>
              <option value="conflict">Conflict detected</option>
              <option value="clear">No conflict</option>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="field-label" htmlFor="audit-since">From</label><Input id="audit-since" type="datetime-local" value={draft.since} onChange={(event) => setDraft({ ...draft, since: event.target.value })} /></div>
            <div><label className="field-label" htmlFor="audit-until">To</label><Input id="audit-until" type="datetime-local" value={draft.until} onChange={(event) => setDraft({ ...draft, until: event.target.value })} /></div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2 md:col-span-2 xl:col-span-4">
            <Button type="button" size="sm" variant="ghost" disabled={activeFilterCount === 0 && Object.values(draft).every((value) => !value)} onClick={reset}>Reset</Button>
            <Button type="submit" size="sm"><Filter className="h-3.5 w-3.5" /> Apply filters</Button>
          </div>
        </div>
      </form>

      {audit.isLoading ? (
        <LoadingState title="Loading audit events" description="Retrieving immutable operator and automation activity." />
      ) : audit.isError ? (
        <EmptyState title="Audit events could not be loaded" description={audit.error instanceof Error ? audit.error.message : 'Try again in a moment.'} action={<Button variant="secondary" onClick={() => void audit.refetch()}>Try again</Button>} />
      ) : !audit.data || audit.data.items.length === 0 ? (
        <EmptyState title="No audit events match these filters" description="Clear the filters or perform an infrastructure action." action={activeFilterCount > 0 ? <Button size="sm" variant="secondary" onClick={reset}>Clear filters</Button> : undefined} />
      ) : (
        <section className="space-y-3" aria-label="Audit events">
          <div className="flex flex-wrap items-center justify-between gap-2 px-1"><div><h2 className="text-sm font-semibold text-[#26312b]">Activity history</h2><p className="mt-0.5 text-[10px] text-[#7b857f]">Select an event for its readable summary and context.</p></div><Badge>{audit.data.items.length} on this page</Badge></div>
          {audit.data.items.map((event, index) => {
            const visual = resultVisual(event.result)
            const ResultIcon = visual.icon
            return (
              <button
                type="button"
                key={event.id}
                aria-label={`Open details for ${eventActionLabel(event)}`}
                onClick={() => setSelected(event)}
                style={{ animationDelay: `${Math.min(index, 8) * 24}ms` }}
                className="group relative flex w-full animate-row-enter items-start gap-3 overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white p-4 text-left shadow-[var(--ui-shadow)] transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-[0_16px_36px_rgba(23,79,64,0.09)] sm:items-center sm:gap-4 sm:p-5"
              >
                <span className={`absolute inset-y-0 left-0 w-1 ${visual.railClass}`} aria-hidden />
                <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ring-1 ring-inset transition-transform duration-200 group-hover:scale-105 ${visual.iconClass}`}><ResultIcon className="h-[18px] w-[18px]" aria-hidden /></span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <span className="min-w-0"><span className="block text-sm font-semibold tracking-[-0.01em] text-[#222b26]">{eventActionLabel(event)}</span><span className="mt-1 block truncate text-xs font-medium text-[#536058]">{displayValue(event.resource_name, 'Platform')}</span></span>
                    <Badge tone={resultTone(event.result)} className="w-fit shrink-0"><ResultIcon className="h-3 w-3" /> {resultLabel(event.result)}</Badge>
                  </span>
                  <span className="mt-3 grid gap-2 border-t border-[#e7eae6] pt-3 text-[11px] text-[#6f7973] sm:grid-cols-3">
                    <span className="flex min-w-0 items-center gap-1.5"><UserRound className="h-3.5 w-3.5 shrink-0 text-brand-700" /><span className="truncate">{event.username ?? 'System automation'}</span></span>
                    <span className="flex items-center gap-1.5 tabular-nums"><Clock3 className="h-3.5 w-3.5 shrink-0 text-brand-700" />{formatDateTime(event.timestamp)}</span>
                    <span className="flex min-w-0 items-center gap-1.5"><Building2 className="h-3.5 w-3.5 shrink-0 text-brand-700" /><span className="truncate">{humanizeIdentifier(event.resource_type)}{event.datacenter_name ? ` · ${event.datacenter_name}` : ''}</span></span>
                  </span>
                </span>
                <ChevronRight className="mt-3 hidden h-4 w-4 shrink-0 text-[#929b95] transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-brand-700 sm:block" />
              </button>
            )
          })}
        </section>
      )}

      {audit.data && audit.data.total > audit.data.page_size && (
        <nav className="flex flex-col gap-3 rounded-2xl border border-[#d8ddd7] bg-white px-4 py-3 text-xs text-[#68736d] shadow-[var(--ui-shadow)] sm:flex-row sm:items-center sm:justify-between" aria-label="Audit pagination">
          <span>Showing {audit.data.items.length} of {audit.data.total} events</span>
          <div className="flex items-center justify-between gap-2 sm:justify-end"><Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</Button><span className="whitespace-nowrap font-medium text-[#465149]">Page {page} of {totalPages}</span><Button size="sm" variant="secondary" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>Next</Button></div>
        </nav>
      )}

      <Dialog open={Boolean(selected)} onClose={() => setSelected(null)} title={selected ? eventActionLabel(selected) : 'Audit event'} wide>
        {selected && <AuditDetails event={selected} />}
      </Dialog>
    </div>
  )
}
