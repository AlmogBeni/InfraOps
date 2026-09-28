import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { applyProgressEvent, applySnapshot } from '@/features/jobs/useJobEvents'
import type { JobDetailOut, JobStepOut } from '@/types/api'

function seededClient(): QueryClient {
  const client = new QueryClient()
  client.setQueryData(['job', 'job-1'], {
    id: 'job-1',
    status: 'RUNNING',
    progress: 10,
    current_stage: 'clone_vm',
    steps: [],
  } as unknown as JobDetailOut)
  return client
}

describe('job event handling', () => {
  it('never copies a step status onto the job status', () => {
    const client = seededClient()
    for (const status of ['SUCCEEDED', 'SKIPPED', 'CANCELLING', 'FAILED']) {
      applyProgressEvent(client, 'job-1', {
        job_id: 'job-1',
        stage: 'configure_hardware',
        status,
        progress: 42,
      })
    }
    const job = client.getQueryData<JobDetailOut>(['job', 'job-1'])
    expect(job?.status).toBe('RUNNING')
    expect(job?.progress).toBe(42)
    expect(job?.current_stage).toBe('configure_hardware')
  })

  it('replaces the step list from a snapshot', () => {
    const client = seededClient()
    const steps = [{ stage_key: 'clone_vm', status: 'SUCCEEDED' }] as unknown as JobStepOut[]
    applySnapshot(client, 'job-1', { job: { progress: 20 }, steps })
    const job = client.getQueryData<JobDetailOut>(['job', 'job-1'])
    expect(job?.steps).toEqual(steps)
    expect(job?.progress).toBe(20)
  })
})
