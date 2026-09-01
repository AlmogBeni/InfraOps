/** React Query hooks for dynamic infrastructure discovery. */

import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'

export function useVcenters(enabled = true) {
  return useQuery({ queryKey: ['vcenters'], queryFn: () => api.vcenters(), enabled })
}

export function useDatacenters(vcenterId: string) {
  return useQuery({
    queryKey: ['datacenters', vcenterId],
    queryFn: () => api.datacenters(vcenterId),
    enabled: Boolean(vcenterId),
  })
}

export function useClusters(vcenterId: string, datacenterId: string) {
  return useQuery({
    queryKey: ['clusters', vcenterId, datacenterId],
    queryFn: () => api.clusters(vcenterId, datacenterId),
    enabled: Boolean(vcenterId && datacenterId),
  })
}

export function useHosts(vcenterId: string, clusterId: string) {
  return useQuery({
    queryKey: ['hosts', vcenterId, clusterId],
    queryFn: () => api.hosts(vcenterId, clusterId),
    enabled: Boolean(vcenterId && clusterId),
  })
}

export function useResourcePools(vcenterId: string, clusterId: string) {
  return useQuery({
    queryKey: ['resource-pools', vcenterId, clusterId],
    queryFn: () => api.resourcePools(vcenterId, clusterId),
    enabled: Boolean(vcenterId && clusterId),
  })
}

export function useDatastores(vcenterId: string, clusterId: string) {
  return useQuery({
    queryKey: ['datastores', vcenterId, clusterId],
    queryFn: () => api.datastores(vcenterId, clusterId),
    enabled: Boolean(vcenterId && clusterId),
  })
}

export function useDatastoreClusters(vcenterId: string, clusterId: string) {
  return useQuery({
    queryKey: ['datastore-clusters', vcenterId, clusterId],
    queryFn: () => api.datastoreClusters(vcenterId, clusterId),
    enabled: Boolean(vcenterId && clusterId),
  })
}

export function useNetworks(vcenterId: string, datacenterId: string | null) {
  return useQuery({
    queryKey: ['networks', vcenterId, datacenterId],
    queryFn: () => api.networks(vcenterId, datacenterId ?? ''),
    enabled: Boolean(vcenterId && datacenterId),
  })
}

export function useTemplates(vcenterId: string, datacenterId: string | null, enabled = true) {
  return useQuery({
    queryKey: ['templates', vcenterId, datacenterId],
    queryFn: () => api.templates(vcenterId, datacenterId ?? ''),
    enabled: Boolean(enabled && vcenterId && datacenterId),
  })
}

export function useIsos(vcenterId: string, datacenterId: string | null, enabled = true) {
  return useQuery({
    queryKey: ['isos', vcenterId, datacenterId],
    queryFn: () => api.isos(vcenterId, datacenterId ?? ''),
    enabled: Boolean(enabled && vcenterId && datacenterId),
  })
}

export function useCertificatePackages(enabled = true) {
  return useQuery({
    queryKey: ['certificate-packages'],
    queryFn: () => api.certificatePackages(true),
    enabled,
  })
}

export function useApplications(enabled = true) {
  return useQuery({ queryKey: ['applications'], queryFn: () => api.applications(true), enabled })
}
