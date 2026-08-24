import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Alert } from '@/components/ui/feedback'
import { Stepper } from '@/components/ui/stepper'
import { useAuth } from '@/lib/auth'
import { api } from '@/lib/api'
import { WizardProvider, WIZARD_STEPS, useWizard } from '@/features/vm-provisioning/context'
import { InfrastructureStep } from '@/features/vm-provisioning/steps/InfrastructureStep'
import { ComputeStep } from '@/features/vm-provisioning/steps/ComputeStep'
import { StorageStep } from '@/features/vm-provisioning/steps/StorageStep'
import { HardwareStep } from '@/features/vm-provisioning/steps/HardwareStep'
import { OsStep } from '@/features/vm-provisioning/steps/OsStep'
import { NetworkStep } from '@/features/vm-provisioning/steps/NetworkStep'
import { CertificatesStep } from '@/features/vm-provisioning/steps/CertificatesStep'
import { ApplicationsStep } from '@/features/vm-provisioning/steps/ApplicationsStep'
import { ReviewStep } from '@/features/vm-provisioning/steps/ReviewStep'

function StepContent() {
  const { currentStep } = useWizard()
  switch (currentStep.key) {
    case 'infrastructure':
      return <InfrastructureStep />
    case 'compute':
      return <ComputeStep />
    case 'storage':
      return <StorageStep />
    case 'hardware':
      return <HardwareStep />
    case 'os':
      return <OsStep />
    case 'network':
      return <NetworkStep />
    case 'certificates':
      return <CertificatesStep />
    case 'applications':
      return <ApplicationsStep />
    default:
      return <ReviewStep />
  }
}

function WizardShell() {
  const wizard = useWizard()
  const navigate = useNavigate()
  const { hasPermission } = useAuth()
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const isReview = wizard.currentStep.key === 'review'
  const canSubmit = hasPermission('provisioning.submit')

  async function handleProvision() {
    setSubmitError(null)
    setSubmitting(true)
    try {
      const payload = wizard.requestPayload()
      const idempotencyKey =
        globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
      const job = await api.submitJob(payload as never, idempotencyKey)
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

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-1 gap-6 lg:grid-cols-[220px_1fr]">
      {/* Persistent progress indicator */}
      <aside>
        <Stepper
          steps={WIZARD_STEPS.map(({ key, title }) => ({ key, title }))}
          currentIndex={wizard.currentIndex}
          onStepClick={wizard.goTo}
        />
      </aside>

      {/* Step body */}
      <div className="min-w-0">
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (!isReview) wizard.next()
          }}
        >
          <StepContent />

          {Object.keys(wizard.errors).length > 0 && (
            <div className="mt-3">
              <Alert tone="danger" title="Please fix the highlighted fields before continuing.">
                <ul className="list-inside list-disc">
                  {Object.entries(wizard.errors).map(([field, message]) => (
                    <li key={field}>{message}</li>
                  ))}
                </ul>
              </Alert>
            </div>
          )}

          {submitError && (
            <div className="mt-3">
              <Alert tone="danger" title="Submission failed">
                {submitError}
              </Alert>
            </div>
          )}

          {/* Footer navigation */}
          <div className="sticky bottom-0 mt-5 flex items-center justify-between gap-2 border-t border-slate-200 bg-white/95 py-3 backdrop-blur">
            <Button
              type="button"
              variant="secondary"
              onClick={wizard.back}
              disabled={wizard.currentIndex === 0}
            >
              Back
            </Button>

            <div className="flex items-center gap-2 text-xs text-slate-400">
              Step {wizard.currentIndex + 1} of {WIZARD_STEPS.length}
            </div>

            {isReview ? (
              <Button
                type="button"
                loading={submitting}
                disabled={!canSubmit}
                title={canSubmit ? undefined : 'Your role cannot provision VMs.'}
                onClick={handleProvision}
              >
                Provision VM
              </Button>
            ) : (
              <Button type="submit">Next</Button>
            )}
          </div>
        </form>
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
