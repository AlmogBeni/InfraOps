import { Navigate, Outlet } from 'react-router-dom'

import { Header } from '@/components/layout/Header'
import { Sidebar } from '@/components/layout/Sidebar'
import { Spinner } from '@/components/ui/feedback'
import { useAuth } from '@/lib/auth'

export function RequireAuth() {
  const { user, initializing } = useAuth()
  if (initializing) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  return (
    <div className="flex h-screen overflow-hidden bg-[#e9eef3]">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main className="flex-1 overflow-y-auto bg-[#e9eef3] p-3 sm:p-4 lg:p-5 xl:p-6">
          <div className="mx-auto w-full max-w-[1800px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
