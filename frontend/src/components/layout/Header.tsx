import { LogOut } from 'lucide-react'
import { useLocation } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'

const TITLES: Array<[string, string]> = [
  ['/provisioning/new', 'VM Provisioning'],
  ['/jobs', 'Jobs'],
  ['/audit', 'Audit Logs'],
  ['/admin/vmware', 'VMware Connections'],
  ['/admin/certificates', 'Certificate Packages'],
  ['/admin/applications', 'Application Catalog'],
  ['/admin/credentials', 'Credentials / Secrets'],
  ['/admin/settings', 'Settings'],
]

export function Header() {
  const location = useLocation()
  const { user, logout } = useAuth()
  const match = TITLES.find(([prefix]) => location.pathname.startsWith(prefix))
  const title = match?.[1] ?? (location.pathname.startsWith('/jobs/') ? 'Job Details' : 'Dashboard')

  async function handleLogout() {
    await logout()
    window.location.href = '/login'
  }

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
      <h1 className="text-base font-semibold text-slate-900">{title}</h1>
      <div className="ml-auto flex items-center gap-3">
        {user && (
          <div className="flex items-center gap-2">
            <div className="hidden text-right sm:block">
              <p className="text-xs font-medium leading-4 text-slate-800">{user.full_name ?? user.username}</p>
              <p className="text-[11px] leading-4 text-slate-500">{user.roles.join(', ')}</p>
            </div>
            <Button variant="ghost" size="sm" onClick={handleLogout} title={`Sign out ${user.username}`}>
              <LogOut className="h-4 w-4" />
              <span className="sr-only">Sign out</span>
            </Button>
          </div>
        )}
      </div>
    </header>
  )
}
