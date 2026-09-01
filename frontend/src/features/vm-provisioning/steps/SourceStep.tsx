import { Box, Layers3 } from 'lucide-react'

import { Alert } from '@/components/ui/feedback'
import { cn } from '@/lib/utils'
import { useWizard } from '@/features/vm-provisioning/context'
import type { VmSourceType } from '@/features/vm-provisioning/schema'

const OPTIONS: Array<{
  value: VmSourceType
  title: string
  description: string
  icon: typeof Box
}> = [
  {
    value: 'blank',
    title: 'Blank Virtual Machine',
    description:
      'Create a new virtual machine and configure its operating system, CPU, memory, storage and networking manually.',
    icon: Box,
  },
  {
    value: 'template',
    title: 'From Template',
    description: 'Deploy a virtual machine using an existing infrastructure template.',
    icon: Layers3,
  },
]

export function SourceStep() {
  const wizard = useWizard()

  return (
    <section aria-label="Virtual machine source" className="space-y-4">
      <header>
        <h2 className="text-base font-semibold text-slate-900">Create Virtual Machine</h2>
        <p className="mt-1 text-sm text-slate-500">How would you like to create this virtual machine?</p>
      </header>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2" role="radiogroup" aria-label="VM source">
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
                'flex min-h-32 items-start gap-3 rounded-lg border bg-white p-4 text-left shadow-sm',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600',
                selected
                  ? 'border-brand-500 ring-1 ring-brand-500'
                  : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50',
              )}
            >
              <span className={cn('rounded-md p-2', selected ? 'bg-brand-50 text-brand-700' : 'bg-slate-100 text-slate-600')}>
                <Icon aria-hidden className="h-5 w-5" />
              </span>
              <span>
                <span className="block text-sm font-semibold text-slate-900">{option.title}</span>
                <span className="mt-1 block text-xs leading-5 text-slate-500">{option.description}</span>
              </span>
            </button>
          )
        })}
      </div>

      {wizard.errors.source_type && (
        <p className="text-xs font-medium text-red-600" role="alert">{wizard.errors.source_type}</p>
      )}

      {wizard.data.source_type === 'blank' && (
        <Alert tone="info" title="Operating system installation is separate">
          The VM will be created with empty disks and left powered off. InfraOps does not currently attach
          installation media, so guest networking, certificates and applications become available after an OS
          is installed outside this workflow.
        </Alert>
      )}
    </section>
  )
}
