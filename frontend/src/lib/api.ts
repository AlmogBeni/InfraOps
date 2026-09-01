/** Typed HTTP client for the InfraOps backend. */

import type {
  ApplicationOut,
  AuditListResponse,
  CertificatePackageOut,
  ClusterOut,
  DashboardResponse,
  DatacenterOut,
  DatastoreClusterOut,
  DatastoreOut,
  HostOut,
  IpConflictReport,
  IsoImageOut,
  JobDetailOut,
  JobListResponse,
  JobOut,
  LogListResponse,
  NetworkOut,
  PlatformSettingsOut,
  PreflightReport,
  ProvisioningRequest,
  ResourcePoolOut,
  RoleOut,
  SecretReferenceOut,
  TemplateOut,
  TokenResponse,
  UserOut,
  VCenterConnectionAdminOut,
  VCenterSummary,
} from '@/types/api'
import { toApiDateTime } from '@/lib/utils'

export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

let accessToken: string | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

interface RequestOptions {
  method?: string
  body?: unknown
  retryOn401?: boolean
  headers?: Record<string, string>
}

async function parseError(response: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = `Request failed with HTTP ${response.status}.`
  let details: unknown
  try {
    const payload = await response.json()
    if (payload?.error) {
      code = payload.error.code ?? code
      message = payload.error.message ?? message
      details = payload.error.details
    }
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(response.status, code, message, details)
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, retryOn401 = true } = options
  const headers: Record<string, string> = { ...(options.headers ?? {}) }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: 'include',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  // The access token intentionally lives in memory. After a full page reload it
  // is absent, but the HttpOnly refresh cookie can still restore the session.
  if (response.status === 401 && retryOn401) {
    const refreshed = await attemptRefresh()
    if (refreshed) return request<T>(path, { ...options, retryOn401: false })
  }

  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

let refreshInFlight: Promise<boolean> | null = null

async function attemptRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(`${API_BASE}/auth/refresh`, {
          method: 'POST',
          credentials: 'include',
        })
        if (!response.ok) return false
        const payload = (await response.json()) as TokenResponse
        accessToken = payload.access_token
        window.dispatchEvent(new CustomEvent<UserOut>('infraops:user', { detail: payload.user }))
        return true
      } catch {
        return false
      } finally {
        refreshInFlight = null
      }
    })()
  }
  return refreshInFlight
}

// ── Endpoint surface ─────────────────────────────────────────────────────────

export const api = {
  // Auth
  async login(username: string, password: string): Promise<TokenResponse> {
    const payload = await request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: { username, password },
      retryOn401: false,
    })
    accessToken = payload.access_token
    return payload
  },

  async logout(): Promise<void> {
    try {
      await request<void>('/auth/logout', { method: 'POST' })
    } finally {
      accessToken = null
    }
  },

  async me(): Promise<UserOut> {
    return request<UserOut>('/auth/me')
  },

  // Infrastructure discovery
  vcenters: () => request<VCenterSummary[]>('/infrastructure/vcenters'),
  datacenters: (vcenterId: string) =>
    request<DatacenterOut[]>(`/infrastructure/vcenters/${vcenterId}/datacenters`),
  clusters: (vcenterId: string, datacenterId: string) =>
    request<ClusterOut[]>(
      `/infrastructure/datacenters/${datacenterId}/clusters?vcenter_id=${vcenterId}`,
    ),
  hosts: (vcenterId: string, clusterId: string) =>
    request<HostOut[]>(`/infrastructure/clusters/${clusterId}/hosts?vcenter_id=${vcenterId}`),
  resourcePools: (vcenterId: string, clusterId: string) =>
    request<ResourcePoolOut[]>(
      `/infrastructure/clusters/${clusterId}/resource-pools?vcenter_id=${vcenterId}`,
    ),
  datastores: (vcenterId: string, clusterId: string) =>
    request<DatastoreOut[]>(`/infrastructure/clusters/${clusterId}/datastores?vcenter_id=${vcenterId}`),
  datastoreClusters: (vcenterId: string, clusterId: string) =>
    request<DatastoreClusterOut[]>(
      `/infrastructure/clusters/${clusterId}/datastore-clusters?vcenter_id=${vcenterId}`,
    ),
  networks: (vcenterId: string, datacenterId: string) =>
    request<NetworkOut[]>(
      `/infrastructure/networks?vcenter_id=${encodeURIComponent(vcenterId)}` +
        `&datacenter_id=${encodeURIComponent(datacenterId)}`,
    ),
  templates: (vcenterId: string, datacenterId: string) =>
    request<TemplateOut[]>(
      `/infrastructure/templates?vcenter_id=${encodeURIComponent(vcenterId)}` +
        `&datacenter_id=${encodeURIComponent(datacenterId)}`,
    ),
  isos: (vcenterId: string, datacenterId: string) =>
    request<IsoImageOut[]>(
      `/infrastructure/isos?vcenter_id=${encodeURIComponent(vcenterId)}` +
        `&datacenter_id=${encodeURIComponent(datacenterId)}`,
    ),

  // Provisioning
  validate: (payload: ProvisioningRequest) =>
    request<PreflightReport>('/provisioning/validate', { method: 'POST', body: payload }),
  ipCheck: (address: string, prefix: number, vcenterId?: string | null) =>
    request<IpConflictReport>(
      '/provisioning/ip-check' + (vcenterId ? `?vcenter_id=${vcenterId}` : ''),
      { method: 'POST', body: { address, prefix } },
    ),
  submitJob: (payload: ProvisioningRequest, idempotencyKey: string) =>
    request<JobOut>('/provisioning/jobs', {
      method: 'POST',
      body: payload,
      headers: { 'Idempotency-Key': idempotencyKey },
    }),
  jobs: (params: { page?: number; page_size?: number; status?: string; vm_name?: string }) => {
    const query = new URLSearchParams()
    if (params.page) query.set('page', String(params.page))
    if (params.page_size) query.set('page_size', String(params.page_size))
    if (params.status) query.set('status', params.status)
    if (params.vm_name) query.set('vm_name', params.vm_name)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<JobListResponse>(`/provisioning/jobs${suffix}`)
  },
  job: (id: string) => request<JobDetailOut>(`/provisioning/jobs/${id}`),
  retryJob: (id: string, stageKey?: string | null) =>
    request<JobOut>(`/provisioning/jobs/${id}/retry`, {
      method: 'POST',
      body: stageKey ? { stage_key: stageKey } : {},
    }),
  cancelJob: (id: string) =>
    request<JobOut>(`/provisioning/jobs/${id}/cancel`, { method: 'POST' }),

  // Catalogs (operator view)
  applications: (enabledOnly = true) =>
    request<ApplicationOut[]>(`/applications?enabled_only=${enabledOnly}`),
  certificatePackages: (enabledOnly = true) =>
    request<CertificatePackageOut[]>(`/certificate-packages?enabled_only=${enabledOnly}`),

  // Administration
  admin: {
    vcenters: () => request<VCenterConnectionAdminOut[]>('/admin/vcenters'),
    createVCenter: (body: Record<string, unknown>) =>
      request<VCenterConnectionAdminOut>('/admin/vcenters', { method: 'POST', body }),
    updateVCenter: (id: string, body: Record<string, unknown>) =>
      request<VCenterConnectionAdminOut>(`/admin/vcenters/${id}`, { method: 'PUT', body }),
    deleteVCenter: (id: string) =>
      request<void>(`/admin/vcenters/${id}`, { method: 'DELETE' }),
    testVCenter: (id: string) =>
      request<{ ok: boolean; latency_ms: number | null; detail: string }>(
        `/admin/vcenters/${id}/test`,
        { method: 'POST' },
      ),


    packages: () => request<CertificatePackageOut[]>('/admin/certificate-packages'),
    createPackage: (body: Record<string, unknown>) =>
      request<CertificatePackageOut>('/admin/certificate-packages', { method: 'POST', body }),
    updatePackage: (id: string, body: Record<string, unknown>) =>
      request<CertificatePackageOut>(`/admin/certificate-packages/${id}`, { method: 'PUT', body }),
    deletePackage: (id: string) =>
      request<void>(`/admin/certificate-packages/${id}`, { method: 'DELETE' }),

    registerCertificate: (body: Record<string, unknown>) =>
      request<Record<string, unknown>>('/admin/certificates', { method: 'POST', body }),
    updateCertificate: (id: string, body: Record<string, unknown>) =>
      request<Record<string, unknown>>(`/admin/certificates/${id}`, { method: 'PUT', body }),
    deleteCertificate: (id: string) =>
      request<void>(`/admin/certificates/${id}`, { method: 'DELETE' }),

    applications: () => request<ApplicationOut[]>('/admin/applications'),
    createApplication: (body: Record<string, unknown>) =>
      request<ApplicationOut>('/admin/applications', { method: 'POST', body }),
    updateApplication: (id: string, body: Record<string, unknown>) =>
      request<ApplicationOut>(`/admin/applications/${id}`, { method: 'PUT', body }),
    deleteApplication: (id: string) =>
      request<void>(`/admin/applications/${id}`, { method: 'DELETE' }),

    credentials: () => request<SecretReferenceOut[]>('/admin/credentials'),
    createCredential: (body: Record<string, unknown>) =>
      request<SecretReferenceOut>('/admin/credentials', { method: 'POST', body }),
    deleteCredential: (id: string) =>
      request<void>(`/admin/credentials/${id}`, { method: 'DELETE' }),

    settings: () => request<PlatformSettingsOut>('/admin/settings'),
    updateSettings: (body: Record<string, unknown>) =>
      request<PlatformSettingsOut>('/admin/settings', { method: 'PUT', body }),
    roles: () => request<RoleOut[]>('/admin/roles'),
  },

  audit: (params: {
    page?: number
    page_size?: number
    action?: string
    username?: string
    job_id?: string
    resource_type?: string
    result?: string
    datacenter?: string
    search?: string
    since?: string
    until?: string
  }) => {
    const query = new URLSearchParams()
    if (params.page) query.set('page', String(params.page))
    if (params.page_size) query.set('page_size', String(params.page_size))
    if (params.action) query.set('action', params.action)
    if (params.username) query.set('username', params.username)
    if (params.job_id) query.set('job_id', params.job_id)
    if (params.resource_type) query.set('resource_type', params.resource_type)
    if (params.result) query.set('result', params.result)
    if (params.datacenter) query.set('datacenter', params.datacenter)
    if (params.search) query.set('search', params.search)
    const since = toApiDateTime(params.since)
    const until = toApiDateTime(params.until)
    if (since) query.set('since', since)
    if (until) query.set('until', until)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<AuditListResponse>(`/audit${suffix}`)
  },

  logs: (params: {
    page?: number
    page_size?: number
    severity?: string
    component?: string
    datacenter?: string
    search?: string
    since?: string
    until?: string
  }) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '') {
        const normalized = key === 'since' || key === 'until' ? toApiDateTime(String(value)) : String(value)
        if (normalized) query.set(key, normalized)
      }
    })
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<LogListResponse>(`/logs${suffix}`)
  },

  dashboard: () => request<DashboardResponse>('/stats/dashboard'),
}
