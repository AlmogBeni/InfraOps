import { describe, expect, it } from 'vitest'

import {
  buildRequest,
  initialWizardData,
  parsePrefixInput,
  validateStep,
  type WizardData,
} from '@/features/vm-provisioning/schema'

function validData(): WizardData {
  const data = initialWizardData()
  data.vcenter_id = '11111111-1111-4111-8111-111111111111'
  data.datacenter_id = 'datacenter-21'
  data.cluster_id = 'domain-c7'
  data.vm_name = 'SERVER-PROD-042'
  data.iso_id = 'iso-corp-windows-2025'
  data.guest_credential_secret_ref = 'guest-admin'
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
    expect(validateStep('location', validData())).toEqual({})
    expect(validateStep('media', validData())).toEqual({})
    expect(validateStep('configuration', validData())).toEqual({})
    expect(validateStep('credentials', validData())).toEqual({})
    expect(validateStep('network', validData())).toEqual({})
    expect(validateStep('directory', validData())).toEqual({})
  })

  it('blocks the location when vCenter is missing', () => {
    const data = validData()
    data.vcenter_id = ''
    const errors = validateStep('location', data)
    expect(errors['vcenter_id']).toBeTruthy()
  })

  it('rejects invalid VM names', () => {
    const data = validData()
    data.vm_name = '-bad name-'
    expect(validateStep('configuration', data)['vm_name']).toBeTruthy()
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

  it('accepts surrounding whitespace in IPv4 fields and normalizes the request', () => {
    const data = validData()
    data.ip_address = ' 192.168.77.52 '
    data.gateway = ' 192.168.77.254 '

    expect(validateStep('network', data)).toEqual({})
    expect(buildRequest(data).network.ipv4?.address).toBe('192.168.77.52')
    expect(buildRequest(data).network.ipv4?.gateway).toBe('192.168.77.254')
  })

  it('uses the actual prefix represented by a dotted subnet mask', () => {
    expect(parsePrefixInput('255.255.254.0')).toBe(23)
    expect(parsePrefixInput('255.0.255.0')).toBeNull()
    expect(parsePrefixInput('0.0.0.0')).toBeNull()
  })

  it('validates every optional DNS server', () => {
    const data = validData()
    data.dns_secondary = '999.1.1.1'
    data.dns_extra = '10.20.1.12, bad-address'

    const errors = validateStep('network', data)
    expect(errors.dns_secondary).toBeTruthy()
    expect(errors.dns_extra).toBeTruthy()
  })

  it('requires the VM name to be a valid short Windows name for domain join', () => {
    const data = validData()
    data.vm_name = 'server.prod'
    data.domain_join = {
      enabled: true,
      domain: 'corp.example.com',
      ou: '',
      credential_secret_ref: 'domain-join',
    }

    expect(validateStep('directory', data).vm_name).toContain('Windows computer name')
  })

  it('accepts a 63-character DNS domain label', () => {
    const data = validData()
    data.domain_join = {
      enabled: true,
      domain: `${'a'.repeat(63)}.example.com`,
      ou: '',
      credential_secret_ref: 'domain-join',
    }

    expect(validateStep('directory', data)['domain_join.domain']).toBeUndefined()
  })

  it('rejects a DNS domain label longer than 63 characters', () => {
    const data = validData()
    data.domain_join = {
      enabled: true,
      domain: `${'a'.repeat(64)}.example.com`,
      ou: '',
      credential_secret_ref: 'domain-join',
    }

    expect(validateStep('directory', data)['domain_join.domain']).toContain('63 characters')
  })

  it('requires a Windows installation ISO', () => {
    const data = validData()
    data.iso_id = ''
    expect(validateStep('media', data).iso_id).toBe('Select the Windows Server installation ISO.')
  })

  it('always requires the provisioning administrator', () => {
    const data = validData()
    data.guest_credential_secret_ref = ''
    expect(validateStep('credentials', data).guest_credential_secret_ref).toBe(
      'Select a Windows provisioning administrator credential.',
    )
  })

  it('requires a host for manual placement', () => {
    const data = validData()
    data.host_mode = 'manual'
    data.host_id = null
    expect(validateStep('location', data).host_id).toBe('Select a host.')
  })

  it('requires a datastore for manual storage placement', () => {
    const data = validData()
    data.storage_mode = 'manual'
    data.datastore_id = null
    expect(validateStep('configuration', data).datastore_id).toBe('Select a datastore.')

    data.storage_mode = 'auto'
    expect(validateStep('configuration', data)).toEqual({})
  })
})

describe('buildRequest', () => {
  it('converts GB to MB and collects DNS servers', () => {
    const data = validData()
    data.memory_gb = 16
    data.dns_secondary = '10.20.1.11'
    data.dns_extra = '10.20.1.12, 10.20.1.13'

    const payload = buildRequest(data)
    expect(payload.identity_policy_version).toBe('v2')
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
    data.vm_name = 'srvildc55'
    data.hostname = 'IGNORED-CUSTOM-NAME'
    data.domain_join = { enabled: true, domain: ' Corp.DeltaGalil.com. ', ou: '', credential_secret_ref: 'domain-join' }
    const joined = buildRequest(data)
    expect(joined.guest.hostname).toBe('SRVILDC55')
    expect(joined.guest.domain_join?.domain).toBe('corp.deltagalil.com')
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

  it('installs Windows from the ISO and runs the complete guest workflow', () => {
    const data = validData()
    data.certificate_package_ids = ['11111111-1111-4111-8111-111111111111']
    data.application_ids = ['22222222-2222-4222-8222-222222222222']
    data.disks = [
      { size_gb: 100, provisioning: 'thin', datastore_id: null },
      { size_gb: 200, provisioning: 'thick', datastore_id: null },
    ]
    data.domain_join.enabled = true
    data.domain_join.domain = 'corp.example.com'

    const payload = buildRequest(data)
    expect(payload.source_type).toBe('blank')
    expect(payload.guest.iso_id).toBe('iso-corp-windows-2025')
    expect(payload.guest.credential_secret_ref).toBe('guest-admin')
    expect(payload.guest.domain_join).not.toBeNull()
    expect(payload.hardware.disks.map((disk) => disk.size_gb)).toEqual([100, 200])
    expect(payload.network.mode).toBe('STATIC')
    expect(payload.certificate_package_ids).toEqual(data.certificate_package_ids)
    expect(payload.application_ids).toEqual(data.application_ids)
  })

  it('sends the product key reference only when one is selected', () => {
    const data = validData()
    expect(buildRequest(data).guest.product_key_secret_ref).toBeNull()

    data.product_key_secret_ref = 'windows-server-2025-standard'
    expect(buildRequest(data).guest.product_key_secret_ref).toBe('windows-server-2025-standard')
  })

  it('sends DHCP without IPv4 settings', () => {
    const data = validData()
    data.ip_mode = 'DHCP'
    const payload = buildRequest(data)
    expect(payload.network.mode).toBe('DHCP')
    expect(payload.network.ipv4).toBeNull()
  })
})
