import {
  Activity,
  ChevronRight,
  ClipboardList,
  FileClock,
  FolderTree,
  LayoutDashboard,
  Lock,
  Package,
  Server,
  ServerCog,
  Settings as SettingsIcon,
  ShieldCheck,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'

import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  icon: typeof LayoutDashboard
}

interface NavSection {
  heading?: string
  items: NavItem[]
}

export function Sidebar() {
  const { hasPermission, hasRole, user } = useAuth()

  const sections: NavSection[] = [
    {
      items: [{ to: '/', label: 'Operations overview', icon: LayoutDashboard }],
    },
    {
      heading: 'Automation',
      items:
        hasPermission('provisioning.submit') || hasPermission('jobs.read')
          ? [{ to: '/provisioning/new', label: 'Provision virtual machine', icon: Server }]
          : [],
    },
    {
      heading: 'Operations',
      items: [
        ...(hasPermission('jobs.read')
          ? [{ to: '/jobs', label: 'Provisioning jobs', icon: ClipboardList }]
          : []),
        ...(hasPermission('audit.read')
          ? [{ to: '/audit', label: 'Audit events', icon: FileClock }]
          : []),
      ],
    },
    {
      heading: 'Administration',
      items: hasRole('administrator')
        ? [
            { to: '/admin/vmware', label: 'vCenter connections', icon: FolderTree },
            { to: '/admin/certificates', label: 'Certificate packages', icon: ShieldCheck },
            { to: '/admin/applications', label: 'Application catalog', icon: Package },
            { to: '/admin/credentials', label: 'Secret references', icon: Lock },
            { to: '/admin/settings', label: 'Platform settings', icon: SettingsIcon },
          ]
        : [],
    },
  ].filter((section) => section.items.length > 0)

  return (
    <aside className="flex w-[268px] shrink-0 flex-col border-r border-slate-800 bg-[#07111f] text-slate-300">
      <div className="flex h-16 items-center gap-3 border-b border-slate-800/90 px-4">
        <span className="flex h-9 w-9 items-center justify-center rounded-md border border-brand-500/50 bg-brand-600/15 text-brand-100">
          <ServerCog className="h-5 w-5" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-bold leading-4 tracking-wide text-white">INFRAOPS</p>
          <p className="mt-0.5 truncate text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-500">
            Virtualization control plane
          </p>
        </div>
      </div>

      <div className="border-b border-slate-800/90 px-4 py-3">
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <Activity className="h-3.5 w-3.5 text-emerald-400" aria-hidden />
          <span className="font-medium text-slate-200">Operator workspace</span>
          <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-400" title="Authenticated" />
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2.5 py-3" aria-label="Main navigation">
        {sections.map((section, index) => (
          <div key={section.heading ?? index} className="mb-5">
            {section.heading && (
              <p className="px-3 pb-1.5 pt-2 text-[9px] font-bold uppercase tracking-[0.16em] text-slate-600">
                {section.heading}
              </p>
            )}
            <ul className="space-y-1">
              {section.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === '/'}
                    className={({ isActive }) =>
                      cn(
                        'group flex h-9 items-center gap-2.5 rounded px-3 text-[13px] transition-colors',
                        isActive
                          ? 'bg-brand-600/15 font-semibold text-white ring-1 ring-inset ring-brand-500/20'
                          : 'text-slate-400 hover:bg-slate-800/70 hover:text-slate-100',
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <item.icon className={cn('h-4 w-4 shrink-0', isActive && 'text-brand-500')} aria-hidden />
                        <span className="min-w-0 flex-1 truncate">{item.label}</span>
                        {isActive && <ChevronRight className="h-3.5 w-3.5 text-brand-500" aria-hidden />}
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-slate-800 px-4 py-3">
        <p className="truncate text-[11px] font-medium text-slate-300">
          {user?.full_name ?? user?.username ?? 'Authenticated operator'}
        </p>
        <p className="mt-0.5 truncate text-[9px] uppercase tracking-wider text-slate-600">
          {user?.roles.join(' · ') ?? 'secure session'}
        </p>
      </div>
    </aside>
  )
}
