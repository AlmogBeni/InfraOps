import { createBrowserRouter, Navigate } from 'react-router-dom'

import { RequireAuth, RequirePermission } from '@/components/layout/AppLayout'
import { LoginPage } from '@/features/auth/LoginPage'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { JobsListPage } from '@/features/jobs/JobsListPage'
import { JobDetailPage } from '@/features/jobs/JobDetailPage'
import { AuditLogPage } from '@/features/audit/AuditLogPage'
import { LogsPage } from '@/features/logs/LogsPage'
import { TemplatesPage } from '@/features/templates/TemplatesPage'
import { VmProvisioningPage } from '@/features/vm-provisioning/VmProvisioningPage'
import { VCenterConnectionsPage } from '@/features/admin/VCenterConnectionsPage'
import { CertificatePackagesPage } from '@/features/admin/CertificatePackagesPage'
import { ApplicationCatalogPage } from '@/features/admin/ApplicationCatalogPage'
import { CredentialsPage } from '@/features/admin/CredentialsPage'
import { SettingsPage } from '@/features/admin/SettingsPage'
import { AdminLayout } from '@/features/admin/AdminLayout'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      { path: '/', element: <DashboardPage /> },
      { path: '/provisioning/new', element: <RequirePermission permission="provisioning.submit"><VmProvisioningPage /></RequirePermission> },
      { path: '/templates', element: <RequirePermission permission="infrastructure.read"><TemplatesPage /></RequirePermission> },
      { path: '/jobs', element: <RequirePermission permission="jobs.read"><JobsListPage /></RequirePermission> },
      { path: '/jobs/:jobId', element: <RequirePermission permission="jobs.read"><JobDetailPage /></RequirePermission> },
      { path: '/logs', element: <RequirePermission permission="jobs.read"><LogsPage /></RequirePermission> },
      { path: '/audit', element: <RequirePermission permission="audit.read"><AuditLogPage /></RequirePermission> },
      {
        path: '/admin',
        element: <AdminLayout />,
        children: [
          { index: true, element: <Navigate to="vmware" replace /> },
          { path: 'vmware', element: <VCenterConnectionsPage /> },
          { path: 'certificates', element: <CertificatePackagesPage /> },
          { path: 'applications', element: <ApplicationCatalogPage /> },
          { path: 'credentials', element: <CredentialsPage /> },
          { path: 'settings', element: <SettingsPage /> },
        ],
      },
      { path: '*', element: <div className="rounded-2xl border border-[#d8ddd7] bg-white p-10 text-center"><h1 className="text-2xl font-semibold">Page not found</h1><p className="mt-2 text-sm text-[#68736d]">The page you requested does not exist or is no longer available.</p></div> },
    ],
  },
])
