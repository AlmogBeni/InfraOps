/** Zod schemas mirroring the backend provisioning contract.
 * The backend re-validates everything; these schemas give instant feedback. */

import { z } from 'zod'

import { maskToPrefix } from '@/lib/utils'
import type { ProvisioningRequest } from '@/types/api'

export const VM_NAME_REGEX = /^[A-Za-z0-9](?:[A-Za-z0-9\-.]{0,61}[A-Za-z0-9])?$/
const IPV4_REGEX = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/

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

export type VmSourceType = 'blank' | 'template'

export interface WizardData {
  source_type: VmSourceType | null
  template_id: string
  vcenter_id: string
  site_id: string
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
    source_type: null,
    template_id: '',
    vcenter_id: '',
    site_id: '',
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
  return z.string().regex(IPV4_REGEX, message)
}

const computeSchema = z.object({
  vm_name: z
    .string()
    .min(2, 'VM name is required.')
    .max(64, 'Maximum 64 characters.')
    .regex(VM_NAME_REGEX, 'Letters, digits, dots and dashes only; must start/end alphanumeric.'),
  cpu: z.number().int().min(1).max(256),
  memory_gb: z.number().int().min(1).max(8192),
  disks: z
    .array(
      z.object({
        size_gb: z.number().int().min(1, 'Minimum 1 GB').max(8000),
        provisioning: z.enum(['thin', 'thick']),
        datastore_id: z.string().nullable(),
      }),
    )
    .min(1, 'At least one disk is required.')
    .max(8, 'Maximum 8 disks.'),
})

export const stepSchemas = {
  source: z.object({
    source_type: z.enum(['blank', 'template'], {
      required_error: 'Choose Blank Virtual Machine or From Template.',
    }),
  }),
  infrastructure: z
    .object({
      source_type: z.enum(['blank', 'template']),
      vcenter_id: z.string().min(1, 'Select a vCenter.'),
      site_id: z.string().min(1, 'Select a site.'),
      datacenter_id: z.string().min(1, 'Select a datacenter.'),
      cluster_id: z.string().min(1, 'Cluster is required.'),
      host_mode: z.enum(['auto', 'manual']),
      host_id: z.string().nullable(),
      template_id: z.string(),
    })
    .superRefine((value, context) => {
      if (value.host_mode === 'manual' && !value.host_id) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['host_id'],
          message: 'Select a host.',
        })
      }
      if (value.source_type === 'template' && !value.template_id) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['template_id'],
          message: 'Select a VM template.',
        })
      }
    }),
  compute: computeSchema,
  storage: z
    .object({
      storage_mode: z.enum(['auto', 'manual']),
      datastore_id: z.string().nullable(),
    })
    .superRefine((value, context) => {
      if (value.storage_mode === 'manual' && !value.datastore_id) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['datastore_id'],
          message: 'Select a datastore.',
        })
      }
    }),
  network: z.object({
    network_id: z.string().min(1, 'Select a port group.'),
    ip_address: ipv4('Enter a valid IPv4 address.'),
    prefix_input: z
      .string()
      .refine(
        (value) => {
          if (/^\d{1,2}$/.test(value)) {
            const prefix = Number(value)
            return prefix >= 8 && prefix <= 32
          }
          return maskToPrefix(value) !== null
        },
        'Enter a prefix length (8–32) or a valid subnet mask.',
      ),
    gateway: ipv4('Enter a valid IPv4 gateway.'),
    dns_primary: ipv4('Enter a valid IPv4 DNS server.'),
  }),
  os: z.object({
    hostname: z.string().regex(VM_NAME_REGEX, 'Invalid hostname.'),
    domain_join: z
      .object({
        enabled: z.boolean(),
        domain: z.string(),
        ou: z.string(),
        credential_secret_ref: z.string(),
      })
      .refine((join) => !join.enabled || join.domain.length >= 3, {
        message: 'Domain is required when joining.',
        path: ['domain'],
      })
      .refine((join) => !join.enabled || join.credential_secret_ref.length >= 2, {
        message: 'Select the domain-join credential reference.',
        path: ['credential_secret_ref'],
      }),
  }),
} satisfies Record<string, z.ZodTypeAny>

export type StepKey = keyof typeof stepSchemas

/** Validate one wizard slice; returns a field-to-message map. */
export function validateStep(step: StepKey, data: WizardData): Record<string, string> {
  const fromTemplate = data.source_type === 'template'
  const slice = {
    source: { source_type: data.source_type },
    infrastructure: {
      source_type: data.source_type,
      vcenter_id: data.vcenter_id,
      site_id: data.site_id,
      datacenter_id: data.datacenter_id,
      cluster_id: data.cluster_id,
      host_mode: data.host_mode,
      host_id: data.host_id,
      template_id: data.template_id,
    },
    compute: { vm_name: data.vm_name, cpu: data.cpu, memory_gb: data.memory_gb, disks: data.disks },
    storage: { storage_mode: data.storage_mode, datastore_id: data.datastore_id },
    network: {
      network_id: data.network_id,
      ip_address: fromTemplate && data.ip_mode === 'STATIC' ? data.ip_address : '0.0.0.0',
      prefix_input: fromTemplate && data.ip_mode === 'STATIC' ? data.prefix_input : '24',
      gateway: fromTemplate && data.ip_mode === 'STATIC' ? data.gateway : '0.0.0.0',
      dns_primary: fromTemplate && data.ip_mode === 'STATIC' ? data.dns_primary : '0.0.0.0',
    },
    os: {
      hostname: fromTemplate ? (data.hostname || data.vm_name) : (data.vm_name || 'blank-vm'),
      domain_join: fromTemplate ? data.domain_join : { ...data.domain_join, enabled: false },
    },
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
  if (/^\d{1,2}$/.test(input.trim())) return Number(input.trim())
  return maskToPrefix(input.trim()) ?? 24
}

function collectDns(data: WizardData): string[] {
  const servers = [data.dns_primary, data.dns_secondary, ...data.dns_extra.split(/[\s,;]+/)]
  return servers.map((entry) => entry.trim()).filter((entry) => entry.length > 0)
}

export function buildRequest(data: WizardData): ProvisioningRequest {
  if (!data.source_type) throw new Error('A VM source must be selected before building the request.')
  const fromTemplate = data.source_type === 'template'
  return {
    source_type: data.source_type,
    vm: { name: data.vm_name, description: data.description },
    compute: {
      vcenter_id: data.vcenter_id,
      site_id: data.site_id,
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
      template_id: fromTemplate ? data.template_id : null,
      hostname: fromTemplate ? (data.hostname || data.vm_name) : null,
      timezone: fromTemplate ? (data.timezone || null) : null,
      domain_join:
        fromTemplate && data.domain_join.enabled
          ? {
              domain: data.domain_join.domain,
              ou: data.domain_join.ou || null,
              credential_secret_ref: data.domain_join.credential_secret_ref,
            }
          : null,
    },
    network: {
      network_id: data.network_id,
      adapter_type: data.adapter_type,
      mode: fromTemplate ? data.ip_mode : 'DHCP',
      ipv4:
        fromTemplate && data.ip_mode === 'STATIC'
          ? {
              address: data.ip_address,
              prefix: resolvePrefix(data.prefix_input),
              gateway: data.gateway,
              dns_servers: collectDns(data),
            }
          : null,
    },
    certificate_package_ids: fromTemplate ? data.certificate_package_ids : [],
    application_ids: fromTemplate ? data.application_ids : [],
  }
}
