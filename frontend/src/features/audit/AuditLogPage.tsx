import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { Badge } from '@/components/ui/feedback'
import { Button } from '@/components/ui/button'
import { EmptyState, Spinner } from '@/components/ui/feedback'
import { Input } from '@/components/ui/form-controls'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'

function resultTone(result: string | null): 'success' | 'danger' | 'warning' | 'neutral' {
  switch (result) {
    case 'success':
    case 'ready':
    case 'clear':
      return 'success'
    case 'failure':
    case 'blocked':
    case 'conflict':
      return 'danger'
    case 'cancelled':
      return 'warning'
    default:
      return 'neutral'
  }
}

export function AuditLogPage() {
  const [page, setPage] = useState(1)
  const [actionFilter, setActionFilter] = useState('')
  const [usernameFilter, setUsernameFilter] = useState('')

  const audit = useQuery({
    queryKey: ['audit', page, actionFilter, usernameFilter],
    queryFn: () =>
      api.audit({
        page,
        action: actionFilter || undefined,
        username: usernameFilter || undefined,
      }),
    refetchInterval: 30_000,
  })

  const totalPages = audit.data ? Math.max(1, Math.ceil(audit.data.total / 50)) : 1

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-64">
          <label className="field-label" htmlFor="audit-action">Action</label>
          <Input id="audit-action" className="font-mono text-xs" placeholder="e.g. JOB_COMPLETED"
                 value={actionFilter} onChange={(event) => { setPage(1); setActionFilter(event.target.value.toUpperCase()) }} />
        </div>
        <div className="w-48">
          <label className="field-label" htmlFor="audit-user">Username contains</label>
          <Input id="audit-user" value={usernameFilter}
                 onChange={(event) => { setPage(1); setUsernameFilter(event.target.value) }} />
        </div>
        <Button variant="secondary" onClick={() => { setActionFilter(''); setUsernameFilter(''); setPage(1) }}>
          Clear filters
        </Button>
      </div>

      {audit.isLoading ? (
        <div className="flex h-48 items-center justify-center"><Spinner /></div>
      ) : !audit.data || audit.data.items.length === 0 ? (
        <EmptyState title="No audit events found" description="Adjust the filters or perform an action first." />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Timestamp</Th>
                <Th>User</Th>
                <Th>Action</Th>
                <Th>Resource</Th>
                <Th>Result</Th>
                <Th>Source IP</Th>
                <Th>Details</Th>
              </tr>
            </thead>
            <tbody>
              {audit.data.items.map((event) => (
                <Tr key={event.id}>
                  <Td className="whitespace-nowrap text-xs">{formatDateTime(event.timestamp)}</Td>
                  <Td className="text-xs">{event.username ?? '—'}</Td>
                  <Td className="font-mono text-[11px] font-medium text-slate-700">{event.action}</Td>
                  <Td className="text-xs">
                    {event.resource_type ? `${event.resource_type}: ` : ''}
                    <span className="font-medium text-slate-700">{event.resource_name ?? '—'}</span>
                  </Td>
                  <Td>
                    <Badge tone={resultTone(event.result)}>{event.result ?? '—'}</Badge>
                  </Td>
                  <Td className="font-mono text-[11px]">{event.source_ip ?? '—'}</Td>
                  <Td>
                    {Object.keys(event.details).length > 0 || event.job_id ? (
                      <details>
                        <summary className="cursor-pointer select-none text-xs text-brand-600">view</summary>
                        <pre className="mt-1 max-w-md overflow-auto whitespace-pre-wrap rounded bg-slate-900 p-2 font-mono text-[10px] leading-relaxed text-slate-100">
{JSON.stringify({ job_id: event.job_id, ...event.details }, null, 2)}
                        </pre>
                      </details>
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>

          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Showing {audit.data.items.length} of {audit.data.total} event(s)</span>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                Previous
              </Button>
              <span>Page {page} / {totalPages}</span>
              <Button size="sm" variant="secondary" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
