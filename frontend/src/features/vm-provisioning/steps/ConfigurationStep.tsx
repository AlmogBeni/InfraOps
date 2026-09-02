import { PackageCheck, SlidersHorizontal } from 'lucide-react'

import { ApplicationsStep } from '@/features/vm-provisioning/steps/ApplicationsStep'
import { CertificatesStep } from '@/features/vm-provisioning/steps/CertificatesStep'
import { HardwareStep } from '@/features/vm-provisioning/steps/HardwareStep'
import { OsStep } from '@/features/vm-provisioning/steps/OsStep'
import { StorageStep } from '@/features/vm-provisioning/steps/StorageStep'
import { useWizard } from '@/features/vm-provisioning/context'

export function ConfigurationStep() {
  const { data } = useWizard()
  return (
    <section aria-label="VM configuration" className="space-y-8">
      <header>
        <p className="console-kicker">Step 4 · Configuration</p>
        <h2>Size and configure the virtual machine</h2>
        <p>Define the machine identity, compute, disks, and storage placement. Package deployments can also include supported guest automation.</p>
      </header>

      <HardwareStep embedded />
      <StorageStep embedded />

      <div className="overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)]">
        <div className="flex items-center gap-3 border-b border-[#e2e6e1] bg-gradient-to-r from-[#f8faf6] to-[#eff6f0] px-5 py-4">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand-50 text-brand-700">
            <SlidersHorizontal className="h-4 w-4" aria-hidden />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-[#202923]">Guest operating system and automation</span>
            <span className="mt-0.5 block text-xs text-[#68736d]">
              {data.source_type === 'template'
                ? 'Hostname, Active Directory domain join, certificates, and approved applications.'
                : 'Review what becomes available after the operating system and VMware Tools are installed.'}
            </span>
          </span>
          <PackageCheck className="h-4 w-4 text-brand-700" aria-hidden />
        </div>
        <div className="space-y-8 bg-[#fafbf8] p-5">
          <OsStep embedded />
          {data.source_type === 'template' && (
            <>
              <CertificatesStep embedded />
              <ApplicationsStep embedded />
            </>
          )}
        </div>
      </div>
    </section>
  )
}
