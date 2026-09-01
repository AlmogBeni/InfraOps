import { Box, Check, Layers3 } from 'lucide-react'

import { Alert, Badge } from '@/components/ui/feedback'
import type { VmSourceType } from '@/features/vm-provisioning/schema'
import { useWizard } from '@/features/vm-provisioning/context'
import { cn } from '@/lib/utils'

const OPTIONS: Array<{
  value: VmSourceType
  title: string
  description: string
  detail: string
  icon: typeof Box
}> = [
  {
    value: 'blank',
    title: 'Blank virtual machine',
    description:
      'Create a new virtual machine and configure its operating system, CPU, memory, storage and networking manually.',
    detail: 'Empty disks · powered off · OS installation required',
    icon: Box,
  },
  {
    value: 'template',
    title: 'Deploy from template',
    description: 'Deploy a virtual machine using an existing infrastructure template.',
    detail: 'Guest customization · certificates · application deployment',
    icon: Layers3,
  },
]

export function SourceStep() {
  const wizard = useWizard()

  return (
    <section aria-label="Virtual machine source" className="space-y-5">
      <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Creation method</p>
            <h2>Choose the VM source</h2>
            <p>Select the provider operation InfraOps should execute.</p>
          </div>
          <Badge tone="neutral">Required</Badge>
        </div>
      </header>

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Source operation</p>
            <p className="console-group-description">This choice controls the available guest-automation stages.</p>
          </div>
        </div>
        <div className="divide-y divide-slate-200" role="radiogroup" aria-label="VM source">
          {OPTIONS.map((option) => {
            const selected = wizard.data.source_type === option.value
            const Icon = option.icon
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => wizard.update({ source_type: option.value })}
                className={cn(
                  'grid w-full grid-cols-[32px_minmax(0,1fr)_24px] items-start gap-3 px-4 py-4 text-left',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand-600',
                  selected ? 'bg-brand-50/70' : 'bg-white hover:bg-slate-50',
                )}
              >
                <span
                  className={cn(
                    'flex h-8 w-8 items-center justify-center rounded-md border',
                    selected
                      ? 'border-brand-200 bg-white text-brand-700'
                      : 'border-slate-200 bg-slate-50 text-slate-500',
                  )}
                >
                  <Icon aria-hidden className="h-4 w-4" />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-slate-900">{option.title}</span>
                  <span className="mt-0.5 block max-w-3xl text-xs leading-5 text-slate-600">{option.description}</span>
                  <span className="mt-1.5 block font-mono text-[10px] uppercase tracking-wide text-slate-500">
                    {option.detail}
                  </span>
                </span>
                <span
                  className={cn(
                    'mt-1 flex h-5 w-5 items-center justify-center rounded-full border',
                    selected ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-300 bg-white',
                  )}
                >
                  {selected && <Check className="h-3 w-3" />}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      {wizard.errors.source_type && (
        <p className="text-xs font-medium text-red-600" role="alert">{wizard.errors.source_type}</p>
      )}

      {wizard.data.source_type === 'blank' && (
        <Alert tone="warning" title="Operating system installation is outside this workflow">
          The VM is created with empty virtual disks and remains powered off. Install an OS and VMware Tools
          before using guest networking, certificate deployment, or application automation.
        </Alert>
      )}
    </section>
  )
}
