import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  HardDrive,
  Network,
  RotateCcw,
  Server,
  ShieldCheck,
} from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Alert, Badge } from '@/components/ui/feedback'
import { Stepper } from '@/components/ui/stepper'
import { ApplicationsStep } from '@/features/vm-provisioning/steps/ApplicationsStep'
import { CertificatesStep } from '@/features/vm-provisioning/steps/CertificatesStep'
import { HardwareStep } from '@/features/vm-provisioning/steps/HardwareStep'
import { InfrastructureStep } from '@/features/vm-provisioning/steps/InfrastructureStep'
import { NetworkStep } from '@/features/vm-provisioning/steps/NetworkStep'
import { OsStep } from '@/features/vm-provisioning/steps/OsStep'
import { ReviewStep } from '@/features/vm-provisioning/steps/ReviewStep'
import { SourceStep } from '@/features/vm-provisioning/steps/SourceStep'
import { StorageStep } from '@/features/vm-provisioning/steps/StorageStep'
import { WizardProvider, WIZARD_STEPS, useWizard } from '@/features/vm-provisioning/context'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'

function StepContent() {
  const { currentStep } = useWizard()
  switch (currentStep.key) {
    case 'source':
      return <SourceStep />
    case 'infrastructure':
      return <InfrastructureStep />
    case 'compute':
      return <HardwareStep />
    case 'storage':
      return <StorageStep />
    case 'network':
      return <NetworkStep />
    case 'os':
      return <OsStep />
    case 'certificates':
      return <CertificatesStep />
    case 'applications':
      return <ApplicationsStep />
    default:
      return <ReviewStep />
  }
}

function SnapshotRow({
  label,
  value,
  ready,
}: {
  label: string
  value: string
  ready: boolean
}) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 first:pt-0 last:pb-0">
      <span
        className={`mt-1 h-2 w-2 shrink-0 rounded-full ${ready ? 'bg-emerald-500' : 'bg-slate-300'}`}
        aria-hidden
      />
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</p>
        <p className="mt-0.5 truncate text-xs font-medium text-slate-800">{value}</p>
      </div>
    </div>
  )
}

function ConfigurationSnapshot() {
  const { data } = useWizard()
  const totalDisk = data.disks.reduce((total, disk) => total + disk.size_gb, 0)
  const source =
    data.source_type === 'template'
      ? 'Template deployment'
      : data.source_type === 'blank'
        ? 'Blank virtual machine'
        : 'Not selected'

  return (
    <aside className="shell-card hidden 2xl:block">
      <div className="border-b border-[#d6def0] bg-gradient-to-r from-[#f8faff] to-[#eef3ff] px-4 py-3">
        <p className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-700">
          Configuration snapshot
        </p>
        <p className="mt-0.5 text-xs text-slate-500">Live draft readiness</p>
      </div>
      <div className="divide-y divide-slate-100 px-4 py-3">
        <SnapshotRow label="Source" value={source} ready={Boolean(data.source_type)} />
        <SnapshotRow label="Virtual machine" value={data.vm_name || 'Name not configured'} ready={Boolean(data.vm_name)} />
        <SnapshotRow
          label="Placement"
          value={data.cluster_id ? (data.host_id ? 'Cluster and host selected' : 'Cluster · automatic host') : 'Target not configured'}
          ready={Boolean(data.vcenter_id && data.datacenter_id && data.cluster_id)}
        />
        <SnapshotRow label="Compute" value={`${data.cpu} vCPU · ${data.memory_gb} GB RAM`} ready={Boolean(data.vm_name)} />
        <SnapshotRow label="Storage" value={`${data.disks.length} disk(s) · ${totalDisk} GB`} ready={data.disks.length > 0} />
        <SnapshotRow label="Network" value={data.network_id ? 'Port group selected' : 'Not configured'} ready={Boolean(data.network_id)} />
      </div>
      <div className="border-t border-[#d6def0] bg-[#f8fbff] px-4 py-3">
        <div className="flex items-start gap-2 text-xs text-slate-600">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
          <span>Inventory and policy checks run again before any infrastructure is created.</span>
        </div>
      </div>
    </aside>
  )
}

function WizardShell() {
  const wizard = useWizard()
  const navigate = useNavigate()
  const { hasPermission } = useAuth()
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const isReview = wizard.currentStep.key === 'review'
  const canSubmit = hasPermission('provisioning.submit')
  const nextStep = WIZARD_STEPS[wizard.currentIndex + 1]

  async function handleProvision() {
    setSubmitError(null)
    setSubmitting(true)
    try {
      const payload = wizard.requestPayload()
      const preflight = await api.validate(payload)
      if (!preflight.ready) {
        const failures = preflight.checks
          .filter((check) => check.status === 'FAIL')
          .map((check) => `${check.label}: ${check.detail}`)
          .join(' ')
        setSubmitError(`${preflight.summary}${failures ? ` ${failures}` : ''}`)
        return
      }
      const idempotencyKey =
        globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
      const job = await api.submitJob(payload, idempotencyKey)
      wizard.reset()
      navigate(`/jobs/${job.id}`)
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : 'The provisioning request could not be submitted.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  function discardDraft() {
    if (window.confirm('Discard the current VM configuration and start again?')) wizard.reset()
  }

  return (
    <div className="mx-auto max-w-[1500px] space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
            <Server className="h-3.5 w-3.5" /> Automation / VMware
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-slate-950">Provision virtual machine</h1>
            <Badge tone="neutral">Draft</Badge>
            {wizard.data.source_type && (
              <Badge tone="info">
                {wizard.data.source_type === 'blank' ? 'Blank VM' : 'Template'}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Define placement and guest configuration, validate the plan, then submit one audited deployment job.
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={discardDraft}>
          <RotateCcw className="h-3.5 w-3.5" /> Discard draft
        </Button>
      </header>

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[210px_minmax(0,1fr)] 2xl:grid-cols-[210px_minmax(0,1fr)_250px]">
        <aside className="shell-card">
          <Stepper
            steps={WIZARD_STEPS.map(({ key, title }) => ({ key, title }))}
            currentIndex={wizard.currentIndex}
            errorKeys={Object.keys(wizard.errors).length > 0 ? [wizard.currentStep.key] : []}
            onStepClick={wizard.goTo}
          />
        </aside>

        <form
          className="shell-card min-w-0 overflow-hidden"
          onSubmit={(event) => {
            event.preventDefault()
            if (!isReview) wizard.next()
          }}
        >
          <div className="provisioning-stage min-h-[430px] p-4 sm:p-5 lg:p-6">
            <StepContent />

            {Object.keys(wizard.errors).length > 0 && (
              <div className="mt-4">
                <Alert tone="danger" title="Configuration requires attention">
                  <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs">
                    {Object.entries(wizard.errors).map(([field, message]) => (
                      <li key={field}>{message}</li>
                    ))}
                  </ul>
                </Alert>
              </div>
            )}

            {submitError && (
              <div className="mt-4">
                <Alert tone="danger" title="Provisioning blocked">{submitError}</Alert>
              </div>
            )}
          </div>

          <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200/80 bg-[#f8fbff] px-4 py-3 sm:px-5">
            <div className="flex items-center gap-3">
              <Button
                type="button"
                variant="secondary"
                onClick={wizard.back}
                disabled={wizard.currentIndex === 0}
              >
                <ArrowLeft className="h-4 w-4" /> Back
              </Button>
              <span className="hidden items-center gap-1.5 text-[11px] text-slate-500 sm:flex">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> Draft saved locally
              </span>
            </div>

            {isReview ? (
              <Button
                type="button"
                loading={submitting}
                disabled={!canSubmit}
                title={canSubmit ? undefined : 'Your role cannot provision VMs.'}
                onClick={handleProvision}
              >
                {wizard.data.source_type === 'blank' ? 'Create Blank VM' : 'Deploy from Template'}
              </Button>
            ) : (
              <Button type="submit">
                Continue{nextStep ? `: ${nextStep.title}` : ''} <ArrowRight className="h-4 w-4" />
              </Button>
            )}
          </div>
        </form>

        <ConfigurationSnapshot />
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-1 text-[11px] text-slate-500">
        <span className="inline-flex items-center gap-1.5"><HardDrive className="h-3.5 w-3.5" /> Capacity checked before deployment</span>
        <span className="inline-flex items-center gap-1.5"><Network className="h-3.5 w-3.5" /> Inventory scoped to selected vCenter</span>
        <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5" /> Explicit confirmation required</span>
      </div>
    </div>
  )
}

export function VmProvisioningPage() {
  return (
    <WizardProvider>
      <WizardShell />
    </WizardProvider>
  )
}
