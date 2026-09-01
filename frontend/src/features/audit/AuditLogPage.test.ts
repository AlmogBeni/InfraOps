import { describe, expect, it } from 'vitest'

import { safeAuditDetails } from './AuditLogPage'

describe('safeAuditDetails', () => {
  it('keeps readable facts while omitting identifiers and protected context', () => {
    expect(safeAuditDetails({
      stage: 'clone_vm',
      network_mode: 'STATIC',
      attempt: 2,
      duration_seconds: 1,
      vm_id: '4e21f74c-fddb-47fe-bd73-332f1473778e',
      credential_secret_ref: 'production-admin',
      artifacts: [{ name: 'guest-output.txt' }],
      technical_error: 'provider exception',
      payload: { token: 'do-not-render' },
    })).toEqual([
      { key: 'stage', label: 'Stage', value: 'Clone Vm' },
      { key: 'network_mode', label: 'Network Mode', value: 'Static' },
      { key: 'attempt', label: 'Attempt', value: '2' },
      { key: 'duration_seconds', label: 'Duration Seconds', value: '1 second' },
    ])
  })

  it('summarizes simple lists and never renders nested objects as raw JSON', () => {
    expect(safeAuditDetails({
      fields: ['cpu_count', 'memory_mb', 'disk_gb', 'network_mode', 'iso_id'],
      checks: [{ name: 'provider' }],
      target: '3c21f74c-fddb-47fe-bd73-332f1473778e',
      enabled: true,
    })).toEqual([
      { key: 'fields', label: 'Fields', value: 'Cpu Count, Memory Mb, Disk Gb, Network Mode (+1 more)' },
      { key: 'enabled', label: 'Enabled', value: 'Yes' },
    ])
  })
})
