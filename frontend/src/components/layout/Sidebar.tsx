import {
  ClipboardList,
  FileClock,
  FolderTree,
  LayoutDashboard,
  Lock,
  Package,
  Server,
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
  const { hasPermission, hasRole } = useAuth()

  const sections: NavSection[] = [
    {
      items: [{ to: '/', label: 'Dashboard', icon: LayoutDashboard }],
    },
    {
      heading: 'Automation',
      items:
        hasPermission('provisioning.submit') || hasPermission('jobs.read')
          ? [{ to: '/provisioning/new', label: 'VM Provisioning', icon: Server }]
          : [],
    },
    {
      heading: 'Operations',
      items: [
        ...(hasPermission('jobs.read')
          ? [{ to: '/jobs', label: 'Jobs', icon: ClipboardList }]
          : []),
        ...(hasPermission('audit.read')
          ? [{ to: '/audit', label: 'Audit Logs', icon: FileClock }]
          : []),
      ],
    },
    {
      heading: 'Administration',
      items: hasRole('administrator')
        ? [
            { to: '/admin/vmware', label: 'VMware Connections', icon: FolderTree },
            { to: '/admin/certificates', label: 'Certificate Packages', icon: ShieldCheck },
            { to: '/admin/applications', label: 'Application Catalog', icon: Package },
            { to: '/admin/credentials', label: 'Credentials / Secrets', icon: Lock },
            { to: '/admin/settings', label: 'Settings', icon: SettingsIcon },
          ]
        : [],
    },
  ].filter((section) => section.items.length > 0)

  return (
    <aside className="flex w-60 shrink-0 flex-col bg-slate-900 text-slate-300">
      <div className="flex h-14 items-center gap-2 border-b border-slate-800 px-4">
        <span className="flex h-7 w-7 items-center justify-center rounded bg-brand-600 font-bold text-white">
          IO
        </span>
        <div>
          <p className="text-sm font-semibold leading-4 text-white">InfraOps</p>
          <p className="text-[10px] uppercase tracking-widest text-slate-500">
            Infrastructure Automation
          </p>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3" aria-label="Main navigation">
        {sections.map((section, index) => (
          <div key={section.heading ?? index} className="mb-4">
            {section.heading && (
              <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                {section.heading}
              </p>
            )}
            <ul className="space-y-0.5">
              {section.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === '/'}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm transition-colors',
                        isActive
                          ? 'bg-slate-800 font-medium text-white'
                          : 'hover:bg-slate-800/60 hover:text-slate-100',
                      )
                    }
                  >
                    <item.icon className="h-4 w-4 shrink-0" aria-hidden />
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-slate-800 px-4 py-3 text-[11px] text-slate-500">
        Internal use only · Phase 1
      </div>
    </aside>
  )
}
