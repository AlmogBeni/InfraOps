import { createBrowserRouter } from 'react-router-dom'

import { RequireAuth } from '@/components/layout/AppLayout'
import { LoginPage } from '@/features/auth/LoginPage'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { JobsListPage } from '@/features/jobs/JobsListPage'
import { JobDetailPage } from '@/features/jobs/JobDetailPage'
import { AuditLogPage } from '@/features/audit/AuditLogPage'
import { VmProvisioningPage } from '@/features/vm-provisioning/VmProvisioningPage'
import { VCenterConnectionsPage } from '@/features/admin/VCenterConnectionsPage'
import { CertificatePackagesPage } from '@/features/admin/CertificatePackagesPage'
import { ApplicationCatalogPage } from '@/features/admin/ApplicationCatalogPage'
import { CredentialsPage } from '@/features/admin/CredentialsPage'
import { SettingsPage } from '@/features/admin/SettingsPage'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      { path: '/', element: <DashboardPage /> },
      { path: '/provisioning/new', element: <VmProvisioningPage /> },
      { path: '/jobs', element: <JobsListPage /> },
      { path: '/jobs/:jobId', element: <JobDetailPage /> },
      { path: '/audit', element: <AuditLogPage /> },
      { path: '/admin/vmware', element: <VCenterConnectionsPage /> },
      { path: '/admin/certificates', element: <CertificatePackagesPage /> },
      { path: '/admin/applications', element: <ApplicationCatalogPage /> },
      { path: '/admin/credentials', element: <CredentialsPage /> },
      { path: '/admin/settings', element: <SettingsPage /> },
    ],
  },
])
