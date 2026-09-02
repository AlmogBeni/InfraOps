/** API contract mirrors of the backend Pydantic schemas. */

export type JobStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'COMPLETED'
  | 'PARTIALLY_COMPLETED'
  | 'FAILED'
  | 'CANCELLED'

export type StepStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'SKIPPED'
  | 'CANCELLED'

export type JobType = 'vm_provisioning'

export interface UserOut {
  id: string
  username: string
  email: string | null
  full_name: string | null
  roles: string[]
  permissions: string[]
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: UserOut
}

export interface VCenterSummary {
  id: string
  name: string
  host: string
  port: number
  enabled: boolean
  connection_state: string
  last_checked_at: string | null
}

export interface DatacenterOut {
  id: string
  name: string
  vcenter_id: string
}

export interface ClusterOut {
  id: string
  name: string
  datacenter_id: string
  drs_enabled: boolean
  hosts_count: number
  total_cpu_cores: number
  total_memory_gb: number
}

export interface HostOut {
  id: string
  name: string
  connection_state: string
  maintenance_mode: boolean
  cpu_usage_percent: number
  memory_usage_percent: number
  available_for_provisioning: boolean
}

export interface ResourcePoolOut {
  id: string
  name: string
  cluster_id: string
}

export interface DatastoreOut {
  id: string
  name: string
  type: string
  capacity_gb: number
  free_gb: number
  usage_percent: number
  accessible: boolean
  datastore_cluster_id: string | null
}

export interface DatastoreClusterOut {
  id: string
  name: string
  capacity_gb: number
  free_gb: number
}

export interface NetworkOut {
  id: string
  name: string
  type: string
  datacenter_id?: string | null
  datacenter_name?: string | null
}

export interface TemplateOut {
  id: string
  name: string
  type: 'OVF' | 'OVA'
  description: string
  datacenter_id: string | null
  datacenter_name: string | null
  storage_name: string | null
  location: string | null
  size_bytes: number | null
  last_modified: string | null
  /** Legacy fields remain optional while old jobs are still readable. */
  os_family?: string | null
  os_version?: string | null
  cpu?: number | null
  memory_mb?: number | null
  disk_size_gb?: number | null
}

export interface IsoImageOut {
  id: string
  name: string
  datacenter_id: string
  datacenter_name: string | null
  datastore_id: string
  datastore_name: string
  path: string
  size_bytes: number | null
  last_modified: string | null
}

// ── Provisioning ─────────────────────────────────────────────────────────────

export type DiskProvisioning = 'thin' | 'thick'
export type FirmwareType = 'BIOS' | 'EFI'
export type IpMode = 'DHCP' | 'STATIC'
export type AdapterType = 'VMXNET3' | 'E1000E'

export interface DiskSpec {
  size_gb: number
  provisioning: DiskProvisioning
  datastore_id: string | null
}

export interface Ipv4Config {
  address: string
  prefix: number
  gateway: string
  dns_servers: string[]
}

export interface DomainJoinSpec {
  domain: string
  ou: string | null
  credential_secret_ref: string
}

export interface ProvisioningRequest {
  source_type: 'blank' | 'template'
  identity_policy_version: 'v1' | 'v2'
  vm: { name: string; description: string }
  compute: {
    vcenter_id: string
    datacenter_id: string
    cluster_id: string
    host_id: string | null
    resource_pool_id: string | null
  }
  hardware: {
    cpu: number
    memory_mb: number
    firmware: FirmwareType
    secure_boot: boolean
    disks: DiskSpec[]
  }
  guest: {
    template_id: string | null
    iso_id: string | null
    hostname: string | null
    timezone: string | null
    domain_join: DomainJoinSpec | null
  }
  network: {
    network_id: string
    adapter_type: AdapterType
    mode: IpMode
    ipv4: Ipv4Config | null
  }
  certificate_package_ids: string[]
  application_ids: string[]
}

export type CheckStatus = 'PASS' | 'WARN' | 'FAIL'

export interface PreflightCheck {
  code: string
  label: string
  status: CheckStatus
  detail: string
  blocking: boolean
}

export interface PreflightReport {
  ready: boolean
  checks: PreflightCheck[]
  summary: string
}

export type ConflictProviderStatus =
  | 'NO_CONFLICT'
  | 'CONFLICT_DETECTED'
  | 'NOT_CONFIGURED'
  | 'ERROR'

export interface ProviderResult {
  provider: string
  status: ConflictProviderStatus
  detail: string
}

export interface IpConflictReport {
  address: string
  prefix: number
  providers: ProviderResult[]
  conflict_detected: boolean
  confidence_note: string
}

// ── Jobs ─────────────────────────────────────────────────────────────────────

export interface JobStepOut {
  id: string
  job_id: string
  stage_key: string
  name: string
  sequence: number
  status: StepStatus
  attempt: number
  max_attempts: number
  retryable: boolean
  started_at: string | null
  finished_at: string | null
  output: string | null
  error_human: string | null
  error_technical: string | null
  artifacts: Record<string, unknown>
}

export interface JobOut {
  id: string
  job_type: JobType
  status: JobStatus
  vm_name: string
  datacenter_id: string | null
  datacenter_name: string | null
  requested_by_username: string | null
  current_stage: string | null
  progress: number
  error_summary: string | null
  cancel_requested: boolean
  queued_at: string | null
  started_at: string | null
  finished_at: string | null
  duration_seconds: number | null
}

export interface JobDetailOut extends JobOut {
  steps: JobStepOut[]
  request_payload: ProvisioningRequest | null
}

export interface JobListResponse {
  items: JobOut[]
  total: number
  page: number
  page_size: number
}

// ── Catalogs ─────────────────────────────────────────────────────────────────

export interface CertificateOut {
  id: string
  package_id: string
  friendly_name: string
  certificate_type: 'ROOT' | 'INTERMEDIATE'
  destination_store: 'Root' | 'CA'
  subject_cn: string
  fingerprint_sha256: string
  not_before: string | null
  not_after: string | null
  file_name: string
  enabled: boolean
}

export interface CertificatePackageOut {
  id: string
  name: string
  description: string
  enabled: boolean
  certificates: CertificateOut[]
  created_at: string | null
}

export type InstallerType = 'MSI' | 'EXE' | 'POWERSHELL'
export type DetectionMethod =
  | 'MSI_PRODUCT_CODE'
  | 'REGISTRY_KEY'
  | 'FILE_EXISTS'
  | 'SERVICE_EXISTS'
  | 'SCRIPT'

export interface ApplicationOut {
  id: string
  name: string
  version: string
  description: string
  installer_type: InstallerType
  installer_path: string
  install_arguments: string
  detection_method: DetectionMethod
  detection_config: Record<string, unknown>
  timeout_seconds: number
  reboot_required: boolean
  enabled: boolean
  dependency_ids: string[]
  created_at: string | null
  updated_at: string | null
}

// ── Admin ────────────────────────────────────────────────────────────────────

export interface VCenterConnectionAdminOut {
  id: string
  name: string
  host: string
  port: number
  username_secret_ref: string
  password_secret_ref: string
  verify_ssl: boolean
  enabled: boolean
  notes: string
  connection_state: string
  last_connection_error: string | null
  last_checked_at: string | null
}

export interface SecretReferenceOut {
  id: string
  name: string
  provider: string
  description: string
  meta: Record<string, unknown>
  created_at: string | null
}

export interface DefaultTimeouts {
  clone_minutes: number
  vmware_tools_minutes: number
  network_configuration_minutes: number
  guest_operations_minutes: number
}

export interface PlatformSettingsOut {
  vm_name_policy_regex: string
  allowed_installer_roots: string[]
  default_timeouts: DefaultTimeouts
  environment_label: string
}

export interface RoleOut {
  id: number
  name: string
  description: string
}

// ── Audit & dashboard ────────────────────────────────────────────────────────

export interface AuditEventOut {
  id: string
  timestamp: string
  user_id: string | null
  username: string | null
  action: string
  action_label?: string | null
  resource_type: string | null
  resource_name: string | null
  job_id: string | null
  result: string | null
  source_ip: string | null
  datacenter_name?: string | null
  datacenter_id?: string | null
  detail_text?: string | null
  details: Record<string, unknown>
}

export interface AuditListResponse {
  items: AuditEventOut[]
  total: number
  page: number
  page_size: number
}

export type LogSeverity = 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | 'DEBUG'

export interface LogEventOut {
  id: string
  timestamp: string
  severity: LogSeverity
  component: string
  message: string
  resource_name: string | null
  datacenter_name: string | null
  job_id: string | null
  details: Record<string, unknown>
}

export interface LogListResponse {
  items: LogEventOut[]
  total: number
  page: number
  page_size: number
}

export interface HealthComponent {
  component: string
  status: string
  detail: string
}

export interface DashboardStats {
  vms_provisioned_this_month: number
  success_rate_percent: number | null
  average_duration_seconds: number | null
  failed_jobs: number
  active_jobs: number
}

export interface DashboardResponse {
  stats: DashboardStats
  recent_jobs: JobOut[]
  health: HealthComponent[]
}
