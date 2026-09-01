import { describe, expect, it } from 'vitest'

import {
  buildRequest,
  initialWizardData,
  validateStep,
  type WizardData,
} from '@/features/vm-provisioning/schema'

function validData(): WizardData {
  const data = initialWizardData()
  data.source_type = 'template'
  data.vcenter_id = '11111111-1111-4111-8111-111111111111'
  data.datacenter_id = 'datacenter-21'
  data.cluster_id = 'domain-c7'
  data.vm_name = 'SERVER-PROD-042'
  data.template_id = 'vm-62'
  data.hostname = 'SERVER-PROD-042'
  data.network_id = 'dvportgroup-51'
  data.ip_address = '10.20.30.45'
  data.prefix_input = '24'
  data.gateway = '10.20.30.1'
  data.dns_primary = '10.20.1.10'
  return data
}

describe('validateStep', () => {
  it('passes for a fully valid draft', () => {
    expect(validateStep('source', validData())).toEqual({})
    expect(validateStep('infrastructure', validData())).toEqual({})
    expect(validateStep('compute', validData())).toEqual({})
    expect(validateStep('os', validData())).toEqual({})
    expect(validateStep('network', validData())).toEqual({})
  })

  it('blocks infrastructure when vCenter missing', () => {
    const data = validData()
    data.vcenter_id = ''
    const errors = validateStep('infrastructure', data)
    expect(errors['vcenter_id']).toBeTruthy()
  })

  it('rejects invalid VM names', () => {
    const data = validData()
    data.vm_name = '-bad name-'
    expect(validateStep('compute', data)['vm_name']).toBeTruthy()
  })

  it('rejects bad IP addresses on the network step', () => {
    const data = validData()
    data.ip_address = '999.0.0.1'
    expect(validateStep('network', data)['ip_address']).toBeTruthy()
  })

  it('accepts subnet masks as prefix input', () => {
    const data = validData()
    data.prefix_input = '255.255.255.0'
    expect(validateStep('network', data)).toEqual({})
  })

  it('skips static fields in DHCP mode', () => {
    const data = validData()
    data.ip_mode = 'DHCP'
    data.ip_address = ''
    data.gateway = ''
    data.dns_primary = ''
    expect(validateStep('network', data)).toEqual({})
  })

  it('requires a template only for template mode', () => {
    const template = validData()
    template.template_id = ''
    expect(validateStep('infrastructure', template).template_id).toBe('Select a VM template.')

    template.source_type = 'blank'
    expect(validateStep('infrastructure', template).template_id).toBeUndefined()
  })

  it('requires a host for manual placement', () => {
    const data = validData()
    data.host_mode = 'manual'
    data.host_id = null
    expect(validateStep('infrastructure', data).host_id).toBe('Select a host.')
  })

  it('requires a datastore for manual storage placement', () => {
    const data = validData()
    data.storage_mode = 'manual'
    data.datastore_id = null
    expect(validateStep('storage', data).datastore_id).toBe('Select a datastore.')

    data.storage_mode = 'auto'
    expect(validateStep('storage', data)).toEqual({})
  })
})

describe('buildRequest', () => {
  it('converts GB to MB and collects DNS servers', () => {
    const data = validData()
    data.memory_gb = 16
    data.dns_secondary = '10.20.1.11'
    data.dns_extra = '10.20.1.12, 10.20.1.13'

    const payload = buildRequest(data)
    expect(payload.hardware.memory_mb).toBe(16384)
    expect(payload.network.ipv4?.dns_servers).toEqual([
      '10.20.1.10',
      '10.20.1.11',
      '10.20.1.12',
      '10.20.1.13',
    ])
  })

  it('resolves dotted masks to prefix lengths', () => {
    const data = validData()
    data.prefix_input = '255.255.0.0'
    expect(buildRequest(data).network.ipv4?.prefix).toBe(16)
  })

  it('forces secure boot off on BIOS firmware', () => {
    const data = validData()
    data.firmware = 'BIOS'
    data.secure_boot = true
    expect(buildRequest(data).hardware.secure_boot).toBe(false)
  })

  it('omits domain join unless enabled', () => {
    const payload = buildRequest(validData())
    expect(payload.guest.domain_join).toBeNull()

    const data = validData()
    data.domain_join = { enabled: true, domain: 'ad.company.local', ou: '', credential_secret_ref: 'domain-join' }
    expect(buildRequest(data).guest.domain_join?.domain).toBe('ad.company.local')
  })

  it('maps manual host placement', () => {
    const data = validData()
    data.host_mode = 'manual'
    data.host_id = 'host-11'
    const payload = buildRequest(data)
    expect(payload.compute.host_id).toBe('host-11')
    expect(payload.compute).not.toHaveProperty('site_id')

    data.host_mode = 'auto'
    expect(buildRequest(data).compute.host_id).toBeNull()
  })

  it('removes all template and guest-only state from blank requests', () => {
    const data = validData()
    data.source_type = 'blank'
    data.template_id = 'vm-stale'
    data.certificate_package_ids = ['11111111-1111-4111-8111-111111111111']
    data.application_ids = ['22222222-2222-4222-8222-222222222222']
    data.domain_join.enabled = true

    const payload = buildRequest(data)
    expect(payload.source_type).toBe('blank')
    expect(payload.guest.template_id).toBeNull()
    expect(payload.guest.domain_join).toBeNull()
    expect(payload.certificate_package_ids).toEqual([])
    expect(payload.application_ids).toEqual([])
  })
})
