import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Clock3, Filter, RefreshCw, Search, ShieldCheck, UserRound } from 'lucide-react'
import { useState } from 'react'
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

function eventActionLabel(event: AuditEventOut): string {
  return displayValue(event.action_label, formatAction(event.action))
}

function AuditDetails({ event }: { event: AuditEventOut }) {
  const details = Object.entries(event.details).filter(([, value]) => value !== null && value !== undefined && value !== '')
  return (
    <div className="space-y-5">
      <div className="grid gap-4 rounded-2xl border border-[#dfe4de] bg-[#f8f9f6] p-4 sm:grid-cols-2">
        <div><p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">Actor</p><p className="mt-1 text-sm font-medium">{event.username ?? 'System automation'}</p></div>
        <div><p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">Action</p><p className="mt-1 text-sm font-medium">{eventActionLabel(event)}</p></div>
        <div><p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">Affected resource</p><p className="mt-1 text-sm font-medium">{event.resource_name ?? 'Platform'}</p></div>
        <div><p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#7b857f]">Result</p><p className="mt-1"><Badge tone={resultTone(event.result)}>{resultLabel(event.result)}</Badge></p></div>
      </div>

      {(event.detail_text || details.length > 0) && (
        <section>
          <h3 className="text-sm font-semibold text-[#202923]">What changed</h3>
          {event.detail_text && <p className="mt-2 rounded-xl bg-[#f5f7f3] p-3 text-xs leading-5 text-[#4f5a53]">{event.detail_text}</p>}
          {details.length > 0 && (
            <dl className="mt-3 grid gap-x-8 gap-y-4 sm:grid-cols-2">
              {details.map(([key, value]) => (
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
          )}
        </section>
      )}

      <section className="border-t border-[#e2e6e1] pt-4">
        <h3 className="text-sm font-semibold text-[#202923]">Event context</h3>
        <dl className="mt-3 grid gap-x-8 gap-y-3 text-xs sm:grid-cols-2">
          <div><dt className="text-[#7b857f]">Timestamp</dt><dd className="mt-1 font-medium">{formatDateTime(event.timestamp)}</dd></div>
          <div><dt className="text-[#7b857f]">Resource type</dt><dd className="mt-1 font-medium">{humanizeIdentifier(event.resource_type)}</dd></div>
          {event.datacenter_name && <div><dt className="text-[#7b857f]">Datacenter</dt><dd className="mt-1 font-medium">{event.datacenter_name}</dd></div>}
          {event.source_ip && <div><dt className="text-[#7b857f]">Source address</dt><dd className="mt-1 font-mono font-medium">{event.source_ip}</dd></div>}
          {event.job_id && <div><dt className="text-[#7b857f]">Related deployment</dt><dd className="mt-1"><Link to={'/jobs/' + event.job_id} className="font-semibold text-brand-700 hover:underline">Open deployment details</Link></dd></div>}
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

  function apply(event: React.FormEvent) {
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
        actions={<Button variant="secondary" size="sm" onClick={() => void audit.refetch()}><RefreshCw className="h-3.5 w-3.5" /> Refresh</Button>}
        meta={<span>{audit.data?.total ?? 0} events · immutable activity history</span>}
      />

      <form className="rounded-2xl border border-[#d8ddd7] bg-white p-4 shadow-[var(--ui-shadow)]" onSubmit={apply}>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-[minmax(220px,1.3fr)_140px_160px_150px_140px_160px_160px_160px_auto] 2xl:items-end">
          <div className="relative">
            <label className="field-label" htmlFor="audit-search">Search</label>
            <Search className="pointer-events-none absolute bottom-3 left-3 h-4 w-4 text-[#87908a]" />
            <Input id="audit-search" className="pl-9" placeholder="Action or resource" value={draft.search} onChange={(event) => setDraft({ ...draft, search: event.target.value })} />
          </div>
          <div>
            <label className="field-label" htmlFor="audit-user">Actor</label>
            <Input id="audit-user" placeholder="Username" value={draft.username} onChange={(event) => setDraft({ ...draft, username: event.target.value })} />
          </div>
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
          <div>
            <label className="field-label" htmlFor="audit-datacenter">Datacenter</label>
            <Input id="audit-datacenter" placeholder="Datacenter name" value={draft.datacenter} onChange={(event) => setDraft({ ...draft, datacenter: event.target.value })} />
          </div>
          <div>
            <label className="field-label" htmlFor="audit-since">From</label>
            <Input id="audit-since" type="datetime-local" value={draft.since} onChange={(event) => setDraft({ ...draft, since: event.target.value })} />
          </div>
          <div>
            <label className="field-label" htmlFor="audit-until">To</label>
            <Input id="audit-until" type="datetime-local" value={draft.until} onChange={(event) => setDraft({ ...draft, until: event.target.value })} />
          </div>
          <div className="flex gap-2"><Button type="submit" size="sm"><Filter className="h-3.5 w-3.5" /> Apply</Button><Button type="button" size="sm" variant="ghost" onClick={reset}>Reset</Button></div>
        </div>
      </form>

      {audit.isLoading ? (
        <LoadingState title="Loading audit events" description="Retrieving immutable operator and automation activity." />
      ) : audit.isError ? (
        <EmptyState title="Audit events could not be loaded" description={audit.error instanceof Error ? audit.error.message : 'Try again in a moment.'} action={<Button variant="secondary" onClick={() => void audit.refetch()}>Try again</Button>} />
      ) : !audit.data || audit.data.items.length === 0 ? (
        <EmptyState title="No audit events match these filters" description="Clear the filters or perform an infrastructure action." />
      ) : (
        <section className="overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)]" aria-label="Audit events">
          <div className="hidden grid-cols-[180px_180px_minmax(190px,0.9fr)_minmax(220px,1.2fr)_130px_24px] gap-4 border-b border-[#dfe4de] bg-[#f7f8f5] px-5 py-3 text-[10px] font-bold uppercase tracking-[0.1em] text-[#727c76] lg:grid">
            <span>When</span><span>Actor</span><span>Action</span><span>Resource</span><span>Result</span><span />
          </div>
          {audit.data.items.map((event) => (
            <button
              type="button"
              key={event.id}
              onClick={() => setSelected(event)}
              className="grid w-full gap-3 border-b border-[#e6e9e5] px-5 py-4 text-left transition-colors last:border-b-0 hover:bg-[#f8f9f6] lg:grid-cols-[180px_180px_minmax(190px,0.9fr)_minmax(220px,1.2fr)_130px_24px] lg:items-center"
            >
              <span className="flex items-center gap-2 text-[11px] tabular-nums text-[#68736d]"><Clock3 className="h-3.5 w-3.5 shrink-0" /> {formatDateTime(event.timestamp)}</span>
              <span className="flex min-w-0 items-center gap-2 text-xs font-medium text-[#364039]"><UserRound className="h-3.5 w-3.5 shrink-0 text-[#87908a]" /><span className="truncate">{event.username ?? 'System automation'}</span></span>
              <span className="text-sm font-semibold text-[#222b26]">{eventActionLabel(event)}</span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-[#303a34]">{event.resource_name ?? 'Platform'}</span>
                <span className="mt-0.5 block text-[11px] text-[#7b857f]">{humanizeIdentifier(event.resource_type)}{event.datacenter_name ? ' · ' + event.datacenter_name : ''}</span>
              </span>
              <Badge tone={resultTone(event.result)}><ShieldCheck className="h-3 w-3" /> {resultLabel(event.result)}</Badge>
              <ChevronRight className="h-4 w-4 text-[#929b95]" />
            </button>
          ))}
        </section>
      )}

      {audit.data && audit.data.total > audit.data.page_size && (
        <div className="flex items-center justify-between text-xs text-[#68736d]">
          <span>Showing {audit.data.items.length} of {audit.data.total} events</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</Button>
            <span>Page {page} of {totalPages}</span>
            <Button size="sm" variant="secondary" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>Next</Button>
          </div>
        </div>
      )}

      <Dialog open={Boolean(selected)} onClose={() => setSelected(null)} title={selected ? eventActionLabel(selected) : 'Audit event'} wide>
        {selected && <AuditDetails event={selected} />}
      </Dialog>
    </div>
  )
}
