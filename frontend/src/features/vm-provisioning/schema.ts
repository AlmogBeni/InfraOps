/** Zod schemas mirroring the backend provisioning contract.
 * The backend re-validates everything; these schemas give instant feedback.
 * Every VM is created empty and Windows Server is installed unattended from an ISO. */

import { z } from 'zod'

import { deriveGuestIdentity, normalizeDomain } from '@/features/vm-provisioning/identity'
import { maskToPrefix } from '@/lib/utils'
import type { ProvisioningRequest } from '@/types/api'

export const VM_NAME_REGEX = /^[A-Za-z0-9](?:[A-Za-z0-9\-.]{0,61}[A-Za-z0-9])?$/
const WINDOWS_COMPUTER_NAME_REGEX = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/
const DNS_DOMAIN_REGEX = /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/i
const IPV4_REGEX = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/

function isWindowsComputerName(value: string): boolean {
  return WINDOWS_COMPUTER_NAME_REGEX.test(value) && !/^\d+$/.test(value)
}

export interface DiskDraft {
  size_gb: number
  provisioning: 'thin' | 'thick'
  datastore_id: string | null
}

export interface DomainJoinDraft {
  enabled: boolean
  domain: string
  ou: string
  credential_secret_ref: string
}

function validateDomainIdentity(
  vmName: string,
  join: DomainJoinDraft,
  context: z.RefinementCtx,
) {
  if (!join.enabled) return
  const domain = normalizeDomain(join.domain)
  if (!DNS_DOMAIN_REGEX.test(domain)) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['domain_join', 'domain'],
      message: 'Enter a valid DNS domain, for example corp.example.com.',
    })
  }
  if (domain.split('.').some((label) => label.length > 63)) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['domain_join', 'domain'],
      message: 'Each DNS domain label must not exceed 63 characters.',
    })
  }
  if (`${vmName}.${domain}`.length > 253) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['domain_join', 'domain'],
      message: 'The resulting fully qualified DNS name must not exceed 253 characters.',
    })
  }
}

/**
 * Microsoft's Windows Server media list their editions in a fixed order:
 * 1 Standard and 3 Datacenter are Server Core, 2 and 4 the same editions with
 * the Desktop Experience. Every server is installed with the Desktop Experience.
 */
export const WINDOWS_EDITIONS = [
  { index: 2, label: 'Standard (Desktop Experience)' },
  { index: 4, label: 'Datacenter (Desktop Experience)' },
] as const

export function isDesktopExperienceEdition(index: number): boolean {
  return WINDOWS_EDITIONS.some((edition) => edition.index === index)
}

export function windowsEditionLabel(index: number): string {
  return WINDOWS_EDITIONS.find((edition) => edition.index === index)?.label ?? `Image index ${index}`
}

export interface WizardData {
  iso_id: string
  /** Credential reference holding a Windows product key; '' when the media needs none. */
  product_key_secret_ref: string
  vcenter_id: string
  datacenter_id: string
  cluster_id: string
  host_mode: 'auto' | 'manual'
  host_id: string | null
  resource_pool_id: string | null
  storage_mode: 'auto' | 'manual'
  datastore_id: string | null
  vm_name: string
  description: string
  cpu: number
  memory_gb: number
  firmware: 'BIOS' | 'EFI'
  secure_boot: boolean
  disks: DiskDraft[]
  hostname: string
  timezone: string
  installation_locale: string
  input_locale: string
  windows_image_index: number
  guest_credential_secret_ref: string
  domain_join: DomainJoinDraft
  network_id: string
  adapter_type: 'VMXNET3' | 'E1000E'
  ip_mode: 'DHCP' | 'STATIC'
  ip_address: string
  prefix_input: string
  gateway: string
  dns_primary: string
  dns_secondary: string
  dns_extra: string
  certificate_package_ids: string[]
  application_ids: string[]
}

export function initialWizardData(): WizardData {
  return {
    iso_id: '',
    product_key_secret_ref: '',
    vcenter_id: '',
    datacenter_id: '',
    cluster_id: '',
    host_mode: 'auto',
    host_id: null,
    resource_pool_id: null,
    storage_mode: 'auto',
    datastore_id: null,
    vm_name: '',
    description: '',
    cpu: 2,
    memory_gb: 8,
    firmware: 'EFI',
    secure_boot: false,
    disks: [{ size_gb: 100, provisioning: 'thin', datastore_id: null }],
    hostname: '',
    timezone: '',
    installation_locale: 'en-US',
    input_locale: '0409:00000409',
    windows_image_index: 2,
    guest_credential_secret_ref: '',
    domain_join: { enabled: false, domain: '', ou: '', credential_secret_ref: 'domain-join' },
    network_id: '',
    adapter_type: 'VMXNET3',
    ip_mode: 'STATIC',
    ip_address: '',
    prefix_input: '24',
    gateway: '',
    dns_primary: '',
    dns_secondary: '',
    dns_extra: '',
    certificate_package_ids: [],
    application_ids: [],
  }
}

function ipv4(message: string) {
  return z.string().trim().regex(IPV4_REGEX, message)
}

function optionalIpv4(message: string) {
  return z.string().trim().refine((value) => value === '' || IPV4_REGEX.test(value), message)
}

function ipv4List(message: string) {
  return z.string().refine(
    (value) => value
      .split(/[\s,;]+/)
      .map((entry) => entry.trim())
      .filter(Boolean)
      .every((entry) => IPV4_REGEX.test(entry)),
    message,
  )
}

export function isValidIpv4(value: string): boolean {
  return IPV4_REGEX.test(value.trim())
}

export function parsePrefixInput(value: string): number | null {
  const input = value.trim()
  if (/^\d{1,2}$/.test(input)) {
    const prefix = Number(input)
    return prefix >= 8 && prefix <= 32 ? prefix : null
  }
  const prefix = maskToPrefix(input)
  return prefix !== null && prefix >= 8 && prefix <= 32 ? prefix : null
}

const diskSchema = z.object({
  size_gb: z.number().int().min(1, 'Minimum 1 GB').max(8000),
  provisioning: z.enum(['thin', 'thick']),
  datastore_id: z.string().nullable(),
})

const domainJoinSchema = z.object({
  enabled: z.boolean(),
  domain: z.string(),
  ou: z.string(),
  credential_secret_ref: z.string(),
})

export const stepSchemas = {
  location: z
    .object({
      vcenter_id: z.string().min(1, 'Select a vCenter.'),
      datacenter_id: z.string().min(1, 'Select a datacenter.'),
      cluster_id: z.string().min(1, 'Select a compute target.'),
      host_mode: z.enum(['auto', 'manual']),
      host_id: z.string().nullable(),
    })
    .superRefine((value, context) => {
      if (value.host_mode === 'manual' && !value.host_id) {
        context.addIssue({ code: z.ZodIssueCode.custom, path: ['host_id'], message: 'Select a host.' })
      }
    }),
  media: z.object({
    iso_id: z.string().min(1, 'Select the Windows Server installation ISO.'),
    product_key_secret_ref: z.string(),
  }),
  configuration: z.object({
    vm_name: z
      .string()
      .min(2, 'VM name is required.')
      .max(64, 'Maximum 64 characters.')
      .regex(VM_NAME_REGEX, 'Letters, digits, dots and dashes only; must start/end alphanumeric.'),
    cpu: z.number().int().min(1).max(256),
    memory_gb: z.number().int().min(1).max(8192),
    disks: z
      .array(diskSchema)
      .min(1, 'At least one disk is required.')
      .max(8, 'Maximum 8 disks.'),
    storage_mode: z.enum(['auto', 'manual']),
    datastore_id: z.string().nullable(),
    hostname: z.string(),
    installation_locale: z.string().trim().min(2, 'Enter a Windows language tag.'),
    input_locale: z.string().trim().min(2, 'Enter a Windows keyboard input locale.'),
    windows_image_index: z
      .number()
      .refine(isDesktopExperienceEdition, 'Choose Windows Server Standard or Datacenter (Desktop Experience).'),
    domain_join: domainJoinSchema,
  }).superRefine((value, context) => {
    if (value.storage_mode === 'manual' && !value.datastore_id) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['datastore_id'],
        message: 'Select a datastore.',
      })
    }
    const hostname = value.hostname || value.vm_name
    if (!isWindowsComputerName(hostname)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['hostname'],
        message: 'Enter a valid short Windows computer name (letters, numbers and hyphens only).',
      })
    }
  }),
  credentials: z.object({
    guest_credential_secret_ref: z
      .string()
      .min(2, 'Select a Windows provisioning administrator credential.'),
  }),
  network: z.object({
    network_id: z.string().min(1, 'Select a port group.'),
    ip_address: ipv4('Enter a valid IPv4 address.'),
    prefix_input: z
      .string()
      .refine(
        (value) => parsePrefixInput(value) !== null,
        'Enter a prefix length (8–32) or a valid subnet mask.',
      ),
    gateway: ipv4('Enter a valid IPv4 gateway.'),
    dns_primary: ipv4('Enter a valid IPv4 DNS server.'),
    dns_secondary: optionalIpv4('Enter a valid secondary IPv4 DNS server.'),
    dns_extra: ipv4List('Enter only valid IPv4 DNS servers separated by spaces or commas.'),
  }),
  directory: z.object({
    vm_name: z.string(),
    domain_join: domainJoinSchema,
  }).superRefine((value, context) => {
    if (!value.domain_join.enabled) return
    if (!isWindowsComputerName(value.vm_name)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['vm_name'],
        message: 'Enter a valid short Windows computer name (letters, numbers and hyphens only).',
      })
    }
    validateDomainIdentity(value.vm_name, value.domain_join, context)
    if (value.domain_join.credential_secret_ref.length < 2) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['domain_join', 'credential_secret_ref'],
        message: 'Select the domain-join credential.',
      })
    }
  }),
} satisfies Record<string, z.ZodTypeAny>

export type StepKey = keyof typeof stepSchemas

/** Validate one wizard slice; returns a field-to-message map. */
export function validateStep(step: StepKey, data: WizardData): Record<string, string> {
  const staticIp = data.ip_mode === 'STATIC'
  const slice = {
    location: {
      vcenter_id: data.vcenter_id,
      datacenter_id: data.datacenter_id,
      cluster_id: data.cluster_id,
      host_mode: data.host_mode,
      host_id: data.host_id,
    },
    media: { iso_id: data.iso_id, product_key_secret_ref: data.product_key_secret_ref },
    configuration: {
      vm_name: data.vm_name,
      cpu: data.cpu,
      memory_gb: data.memory_gb,
      disks: data.disks,
      storage_mode: data.storage_mode,
      datastore_id: data.datastore_id,
      hostname: data.hostname,
      installation_locale: data.installation_locale,
      input_locale: data.input_locale,
      windows_image_index: data.windows_image_index,
      domain_join: data.domain_join,
    },
    credentials: { guest_credential_secret_ref: data.guest_credential_secret_ref },
    network: {
      network_id: data.network_id,
      ip_address: staticIp ? data.ip_address : '0.0.0.0',
      prefix_input: staticIp ? data.prefix_input : '24',
      gateway: staticIp ? data.gateway : '0.0.0.0',
      dns_primary: staticIp ? data.dns_primary : '0.0.0.0',
      dns_secondary: staticIp ? data.dns_secondary : '',
      dns_extra: staticIp ? data.dns_extra : '',
    },
    directory: { vm_name: data.vm_name, domain_join: data.domain_join },
  }[step]

  const result = stepSchemas[step].safeParse(slice)
  if (result.success) return {}
  const errors: Record<string, string> = {}
  for (const issue of result.error.issues) {
    const key = issue.path.join('.') || 'form'
    if (!errors[key]) errors[key] = issue.message
  }
  return errors
}

function resolvePrefix(input: string): number {
  return parsePrefixInput(input) ?? 24
}

function collectDns(data: WizardData): string[] {
  const servers = [data.dns_primary, data.dns_secondary, ...data.dns_extra.split(/[\s,;]+/)]
  return servers.map((entry) => entry.trim()).filter((entry) => entry.length > 0)
}

export function buildRequest(data: WizardData): ProvisioningRequest {
  const identity = deriveGuestIdentity(
    data.vm_name,
    data.hostname,
    data.domain_join.enabled ? data.domain_join.domain : null,
  )
  return {
    source_type: 'blank',
    identity_policy_version: 'v2',
    vm: { name: data.vm_name, description: data.description },
    compute: {
      vcenter_id: data.vcenter_id,
      datacenter_id: data.datacenter_id,
      cluster_id: data.cluster_id,
      host_id: data.host_mode === 'manual' ? data.host_id : null,
      resource_pool_id: data.resource_pool_id,
    },
    hardware: {
      cpu: data.cpu,
      memory_mb: data.memory_gb * 1024,
      firmware: data.firmware,
      secure_boot: data.secure_boot && data.firmware === 'EFI',
      disks: data.disks.map((disk) => ({
        size_gb: disk.size_gb,
        provisioning: disk.provisioning,
        datastore_id: data.storage_mode === 'manual' ? data.datastore_id : disk.datastore_id,
      })),
    },
    guest: {
      iso_id: data.iso_id,
      product_key_secret_ref: data.product_key_secret_ref || null,
      hostname: identity.computerName,
      timezone: data.timezone || null,
      installation_locale: data.installation_locale,
      input_locale: data.input_locale,
      windows_image_index: data.windows_image_index,
      credential_secret_ref: data.guest_credential_secret_ref,
      domain_join: data.domain_join.enabled
        ? {
            domain: normalizeDomain(data.domain_join.domain),
            ou: data.domain_join.ou || null,
            credential_secret_ref: data.domain_join.credential_secret_ref,
          }
        : null,
    },
    network: {
      network_id: data.network_id,
      adapter_type: data.adapter_type,
      mode: data.ip_mode,
      ipv4:
        data.ip_mode === 'STATIC'
          ? {
              address: data.ip_address.trim(),
              prefix: resolvePrefix(data.prefix_input),
              gateway: data.gateway.trim(),
              dns_servers: collectDns(data),
            }
          : null,
    },
    certificate_package_ids: data.certificate_package_ids,
    application_ids: data.application_ids,
  }
}
