import {
  Boxes,
  ChevronDown,
  ClipboardList,
  FileClock,
  FileArchive,
  Gauge,
  LogOut,
  Plus,
  Settings,
  ScrollText,
} from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'

const PRIMARY_LINKS = [
  { to: '/', label: 'Overview', icon: Gauge, end: true },
  { to: '/provisioning/new', label: 'Create VM', icon: Plus, end: false },
  { to: '/templates', label: 'Templates', icon: FileArchive, end: false },
  { to: '/jobs', label: 'Deployments', icon: ClipboardList, end: false },
  { to: '/logs', label: 'Logs', icon: ScrollText, end: false },
  { to: '/audit', label: 'Audit', icon: FileClock, end: false },
]

const ADMIN_LINKS = [
  { to: '/admin/vmware', label: 'vCenter connections' },
  { to: '/admin/certificates', label: 'Certificate packages' },
  { to: '/admin/applications', label: 'Application catalog' },
  { to: '/admin/credentials', label: 'Credential references' },
  { to: '/admin/settings', label: 'Platform settings' },
]

export function Header() {
  const { user, logout, hasPermission, hasRole } = useAuth()
  const location = useLocation()
  const adminActive = location.pathname.startsWith('/admin/')

  async function handleLogout() {
    await logout()
    window.location.href = '/login'
  }

  return (
    <header className="sticky top-0 z-30 border-b border-white/10 bg-[#151a19] text-white shadow-[0_12px_40px_rgba(21,26,25,0.16)]">
      <div className="mx-auto flex min-h-[72px] max-w-[1664px] items-center gap-4 px-4 sm:px-6 lg:px-8">
        <NavLink to="/" className="group flex shrink-0 items-center gap-3" aria-label="InfraOps home">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-[#d8f06a] text-[#18201c] shadow-[0_0_0_1px_rgba(255,255,255,0.12)]">
            <Boxes className="h-[19px] w-[19px]" strokeWidth={2.2} aria-hidden />
          </span>
          <span className="hidden sm:block">
            <span className="block text-[15px] font-bold leading-4 tracking-[-0.02em]">InfraOps</span>
            <span className="mt-0.5 block text-[9px] font-semibold uppercase tracking-[0.18em] text-white/45">
              Virtual estate
            </span>
          </span>
        </NavLink>

        <nav className="ml-1 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto py-2" aria-label="Primary navigation">
          {PRIMARY_LINKS.filter((link) => {
            if (link.to === '/provisioning/new') return hasPermission('provisioning.submit')
            if (link.to === '/jobs') return hasPermission('jobs.read')
            if (link.to === '/logs') return hasPermission('jobs.read')
            if (link.to === '/templates') return hasPermission('infrastructure.read')
            if (link.to === '/audit') return hasPermission('audit.read')
            return true
          }).map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) => cn(
                'group inline-flex h-9 shrink-0 items-center gap-2 rounded-xl px-3 text-xs font-semibold transition-[transform,background-color,color,box-shadow] duration-200 hover:-translate-y-0.5',
                isActive ? 'bg-[#d8f06a] text-[#17201c] shadow-[0_8px_22px_rgba(216,240,106,0.13)]' : 'text-white/60 hover:bg-white/10 hover:text-white',
              )}
            >
              <link.icon className="h-3.5 w-3.5 transition-transform duration-200 group-hover:scale-110" aria-hidden />
              {link.label}
            </NavLink>
          ))}
        </nav>

        {hasRole('administrator') && (
          <details className="group relative block">
            <summary
              className={cn(
                'flex h-9 cursor-pointer list-none items-center gap-2 rounded-lg px-2 text-xs font-semibold transition-colors sm:px-3 [&::-webkit-details-marker]:hidden',
                adminActive ? 'bg-[#d8f06a] text-[#17201c]' : 'text-white/60 hover:bg-white/10 hover:text-white',
              )}
            >
              <Settings className="h-3.5 w-3.5" aria-hidden />
              <span className="hidden xl:inline">Manage</span>
              <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" aria-hidden />
            </summary>
            <nav
              aria-label="Administration"
              className="absolute right-0 top-11 w-64 overflow-hidden rounded-xl border border-[#d8ddd7] bg-white p-1.5 text-[#243029] shadow-[0_18px_50px_rgba(20,28,24,0.22)]"
            >
              <p className="px-3 pb-2 pt-1.5 text-[9px] font-bold uppercase tracking-[0.14em] text-[#7b857f]">Administration</p>
              {ADMIN_LINKS.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  onClick={(event) => event.currentTarget.closest('details')?.removeAttribute('open')}
                  className={({ isActive }) => cn(
                    'block rounded-lg px-3 py-2.5 text-xs font-semibold transition-colors',
                    isActive ? 'bg-brand-50 text-brand-800' : 'text-[#4f5a53] hover:bg-[#f3f5f1] hover:text-[#202923]',
                  )}
                >
                  {link.label}
                </NavLink>
              ))}
            </nav>
          </details>
        )}

        <div className="h-7 w-px bg-white/10" />
        <div className="flex shrink-0 items-center gap-2">
          <div className="hidden text-right md:block">
            <p className="max-w-36 truncate text-[11px] font-semibold text-white">
              {user?.full_name ?? user?.username ?? 'Operator'}
            </p>
            <p className="mt-0.5 flex items-center justify-end gap-1 text-[9px] font-medium uppercase tracking-[0.12em] text-white/40">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              {user?.roles[0] ?? 'Secure session'}
              <ChevronDown className="h-3 w-3" aria-hidden />
            </p>
          </div>
          <Button variant="ghost-inverse" size="icon" onClick={handleLogout} title={`Sign out ${user?.username ?? ''}`}>
            <LogOut className="h-4 w-4" />
            <span className="sr-only">Sign out</span>
          </Button>
        </div>
      </div>
    </header>
  )
}
