import { Navigate, Outlet } from 'react-router-dom'
import type { ReactNode } from 'react'

import { Header } from '@/components/layout/Header'
import { LoadingState } from '@/components/ui/feedback'
import { IdleSessionDialog } from '@/features/auth/IdleSessionDialog'
import { InsecureTransportNotice } from '@/features/auth/InsecureTransportNotice'
import { useAuth } from '@/lib/auth'

export function RequireAuth() {
  const { user, initializing } = useAuth()
  if (initializing) {
    return (
      <div className="min-h-screen bg-stone-100">
        <LoadingState title="Preparing your workspace" description="Checking your secure session." fullPage />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  return (
    <div className="layout-shell min-h-screen">
      <Header />
      <IdleSessionDialog />
      <main className="px-4 pb-12 pt-6 sm:px-6 lg:px-8 lg:pt-8">
        <div className="mx-auto w-full max-w-[1600px]">
          <div className="mb-4 empty:hidden">
            <InsecureTransportNotice />
          </div>
          <Outlet />
        </div>
      </main>
    </div>
  )
}

export function RequirePermission({
  permission,
  children,
}: {
  permission: string
  children: ReactNode
}) {
  const { hasPermission } = useAuth()
  return hasPermission(permission) ? children : <Navigate to="/" replace />
}
