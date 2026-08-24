import { LogOut } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'

import { Badge } from '@/components/ui/feedback'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { API_BASE } from '@/lib/api'

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
  const [environment, setEnvironment] = useState<{ label: string; mode: string }>({
    label: import.meta.env.VITE_ENVIRONMENT_LABEL ?? '',
    mode: '',
  })

  useEffect(() => {
    let cancelled = false
    fetch('/health')
      .then((response) => response.json())
      .then((payload: { environment?: string; infrastructure_mode?: string }) => {
        if (cancelled || !payload?.environment) return
        setEnvironment({
          label:
            import.meta.env.VITE_ENVIRONMENT_LABEL ??
            payload.environment.toUpperCase().slice(0, 8),
          mode: payload.infrastructure_mode ?? '',
        })
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  const match = TITLES.find(([prefix]) => location.pathname.startsWith(prefix))
  const title =
    match?.[1] ?? (location.pathname.startsWith('/jobs/') ? 'Job Details' : 'Dashboard')

  async function handleLogout() {
    await logout()
    window.location.href = '/login'
  }

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
      <h1 className="text-base font-semibold text-slate-900">{title}</h1>

      <div className="ml-auto flex items-center gap-3">
        {environment.label && (
          <Badge tone={environment.mode === 'mock' ? 'warning' : 'info'}>
            {environment.label}
            {environment.mode ? ` · ${environment.mode.toUpperCase()}` : ''}
          </Badge>
        )}
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

export { API_BASE }
