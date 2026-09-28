/** Subscribes to the server-sent event stream for a provisioning job and
 * keeps the React Query cache live without manual refreshes.
 *
 * Authentication: EventSource cannot send an Authorization header, and access
 * tokens must never appear in URLs. Each connection therefore uses a
 * single-use ticket obtained with the (auto-refreshed) bearer token. The
 * browser's built-in reconnect would replay the consumed ticket, so the hook
 * closes the source on error and reconnects itself with a fresh ticket and
 * exponential backoff.
 *
 * State: events carry either a step status or a job status in `status`, so
 * they are never copied onto the job badge. Each event updates progress and
 * the current stage immediately and schedules a refetch of the job detail,
 * which is the source of truth for job and step state.
 */

import { useEffect } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'

import { API_BASE, api } from '@/lib/api'
import type { JobDetailOut, JobStepOut } from '@/types/api'

interface JobEvent {
  job_id: string
  stage: string | null
  status: string
  progress: number
  message?: string
}

const REFETCH_DEBOUNCE_MS = 400
const MAX_BACKOFF_MS = 30_000

export function applySnapshot(
  queryClient: QueryClient,
  jobId: string,
  data: { job: Partial<JobDetailOut>; steps: JobStepOut[] },
): void {
  const existing = queryClient.getQueryData<JobDetailOut>(['job', jobId])
  if (existing) {
    queryClient.setQueryData<JobDetailOut>(['job', jobId], {
      ...existing,
      ...data.job,
      steps: data.steps,
    })
  } else {
    void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
  }
}

export function applyProgressEvent(queryClient: QueryClient, jobId: string, update: JobEvent): void {
  const existing = queryClient.getQueryData<JobDetailOut>(['job', jobId])
  if (!existing) return
  queryClient.setQueryData<JobDetailOut>(['job', jobId], {
    ...existing,
    progress: typeof update.progress === 'number' ? update.progress : existing.progress,
    current_stage: update.stage ?? existing.current_stage,
  })
}

export function useJobEvents(jobId: string, enabled: boolean): void {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!enabled) return

    let source: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let refetchTimer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let disposed = false

    const scheduleRefetch = () => {
      clearTimeout(refetchTimer)
      refetchTimer = setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
      }, REFETCH_DEBOUNCE_MS)
    }

    const scheduleReconnect = (immediate = false) => {
      source?.close()
      source = null
      if (disposed) return
      const delay = immediate ? 0 : Math.min(MAX_BACKOFF_MS, 1_000 * 2 ** Math.min(attempt, 5))
      attempt += 1
      clearTimeout(reconnectTimer)
      reconnectTimer = setTimeout(() => void connect(), delay)
    }

    const connect = async () => {
      let ticket: string
      try {
        ticket = (await api.jobEventsTicket(jobId)).ticket
      } catch {
        scheduleReconnect()
        return
      }
      if (disposed) return

      const next = new EventSource(
        `${API_BASE}/provisioning/jobs/${jobId}/events?ticket=${encodeURIComponent(ticket)}`,
        { withCredentials: true },
      )
      source = next

      next.addEventListener('snapshot', (event) => {
        attempt = 0
        try {
          applySnapshot(queryClient, jobId, JSON.parse((event as MessageEvent).data))
        } catch {
          /* ignore malformed snapshots */
        }
      })

      next.addEventListener('job-update', (event) => {
        try {
          applyProgressEvent(queryClient, jobId, JSON.parse((event as MessageEvent).data) as JobEvent)
        } catch {
          /* ignore malformed events */
        }
        scheduleRefetch()
      })

      // The server ends streams periodically so access is re-checked.
      next.addEventListener('reauthenticate', () => scheduleReconnect(true))

      next.onerror = () => scheduleReconnect()
    }

    void connect()

    return () => {
      disposed = true
      clearTimeout(reconnectTimer)
      clearTimeout(refetchTimer)
      source?.close()
    }
  }, [jobId, enabled, queryClient])
}
