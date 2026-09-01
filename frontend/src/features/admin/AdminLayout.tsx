import { AppWindow, Boxes, KeyRound, ServerCog, Settings2, ShieldCheck } from 'lucide-react'
import { Navigate, NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'

const sections = [
  { to: '/admin/vmware', label: 'vCenter', icon: ServerCog },
  { to: '/admin/certificates', label: 'Certificates', icon: ShieldCheck },
  { to: '/admin/applications', label: 'Applications', icon: AppWindow },
  { to: '/admin/credentials', label: 'Credentials', icon: KeyRound },
  { to: '/admin/settings', label: 'Settings', icon: Settings2 },
]

export function AdminLayout() {
  const { hasRole } = useAuth()
  if (!hasRole('administrator')) return <Navigate to="/" replace />

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-2xl border border-[#d8ddd7] bg-[#17201c] px-5 py-4 text-white shadow-[var(--ui-shadow)] lg:flex-row lg:items-center">
        <div className="flex shrink-0 items-center gap-3 lg:w-64">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-[#d8f06a] text-[#17201c]">
            <Boxes className="h-4 w-4" aria-hidden />
          </span>
          <div>
            <p className="text-xs font-bold">Platform management</p>
            <p className="mt-0.5 text-[10px] text-white/50">Restricted administrator workspace</p>
          </div>
        </div>
        <nav className="flex min-w-0 flex-1 gap-1 overflow-x-auto" aria-label="Platform management sections">
          {sections.map((section) => (
            <NavLink
              key={section.to}
              to={section.to}
              className={({ isActive }) => cn(
                'inline-flex h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-[11px] font-semibold transition-colors',
                isActive ? 'bg-white text-[#17201c]' : 'text-white/55 hover:bg-white/10 hover:text-white',
              )}
            >
              <section.icon className="h-3.5 w-3.5" aria-hidden />
              {section.label}
            </NavLink>
          ))}
        </nav>
      </section>
      <Outlet />
    </div>
  )
}
