/** Subscribes to the server-sent event stream for a provisioning job and
 * keeps the React Query cache live without manual refreshes. */

import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { API_BASE, getAccessToken } from '@/lib/api'
import type { JobDetailOut, JobStatus, JobStepOut } from '@/types/api'

const TERMINAL_STATUSES: JobStatus[] = [
  'COMPLETED',
  'PARTIALLY_COMPLETED',
  'ACTION_REQUIRED',
  'FAILED',
  'CANCELLED',
]

interface JobEvent {
  job_id: string
  stage: string | null
  status: string
  progress: number
  message?: string
}

export function useJobEvents(jobId: string, enabled: boolean): void {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!enabled) return

    const token = getAccessToken()
    const url =
      `${API_BASE}/provisioning/jobs/${jobId}/events` +
      (token ? `?access_token=${encodeURIComponent(token)}` : '')

    const source = new EventSource(url, { withCredentials: true })

    source.addEventListener('snapshot', (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data) as {
          job: Partial<JobDetailOut>
          steps: JobStepOut[]
        }
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
      } catch {
        /* ignore malformed snapshots */
      }
    })

    source.addEventListener('job-update', (event) => {
      try {
        const update = JSON.parse((event as MessageEvent).data) as JobEvent
        const existing = queryClient.getQueryData<JobDetailOut>(['job', jobId])
        if (existing) {
          queryClient.setQueryData<JobDetailOut>(['job', jobId], {
            ...existing,
            status: update.status as JobStatus,
            progress: update.progress,
            current_stage: update.stage ?? existing.current_stage,
          })
        }
        if (TERMINAL_STATUSES.includes(update.status as JobStatus)) {
          // Fetch the definitive final state (step outputs, artifacts).
          void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
        }
      } catch {
        /* ignore malformed events */
      }
    })

    source.onerror = () => {
      /* EventSource auto-reconnects; nothing to do */
    }

    return () => source.close()
  }, [jobId, enabled, queryClient])
}
