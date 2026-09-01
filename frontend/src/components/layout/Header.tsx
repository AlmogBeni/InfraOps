import { ChevronRight, LogOut, ShieldCheck } from 'lucide-react'
import { useLocation } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'

const TITLES: Array<[string, string]> = [
  ['/provisioning/new', 'Provision virtual machine'],
  ['/jobs', 'Provisioning jobs'],
  ['/audit', 'Audit events'],
  ['/admin/vmware', 'vCenter connections'],
  ['/admin/certificates', 'Certificate packages'],
  ['/admin/applications', 'Application catalog'],
  ['/admin/credentials', 'Secret references'],
  ['/admin/settings', 'Platform settings'],
]

export function Header() {
  const location = useLocation()
  const { user, logout } = useAuth()
  const match = TITLES.find(([prefix]) => location.pathname.startsWith(prefix))
  const title = match?.[1] ?? (location.pathname.startsWith('/jobs/') ? 'Job details' : 'Operations overview')

  async function handleLogout() {
    await logout()
    window.location.href = '/login'
  }

  return (
    <header className="sticky top-0 z-20 flex h-12 items-center gap-3 border-b border-slate-300 bg-[#f8fafb] px-4 sm:px-5">
      <div className="flex min-w-0 items-center gap-2 text-xs">
        <span className="font-medium text-slate-400">Control plane</span>
        <ChevronRight className="h-3.5 w-3.5 text-slate-300" aria-hidden />
        <h1 className="truncate font-semibold text-slate-800">{title}</h1>
      </div>
      <div className="ml-auto flex items-center gap-3">
        {user && (
          <div className="flex items-center gap-2 border-l border-slate-300 pl-3">
            <span className="hidden items-center gap-1.5 rounded border border-slate-200 bg-white px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500 sm:inline-flex">
              <ShieldCheck className="h-3 w-3 text-emerald-600" aria-hidden />
              Authenticated
            </span>
            <div className="hidden text-right md:block">
              <p className="text-[11px] font-semibold leading-4 text-slate-800">{user.full_name ?? user.username}</p>
              <p className="text-[9px] uppercase leading-3 tracking-wide text-slate-500">{user.roles.join(', ')}</p>
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
