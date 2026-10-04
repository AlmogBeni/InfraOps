import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  RotateCcw,
  Server,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Alert, Badge } from '@/components/ui/feedback'
import { Stepper } from '@/components/ui/stepper'
import { WizardProvider, WIZARD_STEPS, useWizard } from '@/features/vm-provisioning/context'
import { ConfigurationStep } from '@/features/vm-provisioning/steps/ConfigurationStep'
import { CredentialsStep } from '@/features/vm-provisioning/steps/CredentialsStep'
import { DirectoryStep } from '@/features/vm-provisioning/steps/DirectoryStep'
import { LocationStep } from '@/features/vm-provisioning/steps/LocationStep'
import { MediaStep } from '@/features/vm-provisioning/steps/MediaStep'
import { NetworkStep } from '@/features/vm-provisioning/steps/NetworkStep'
import { ReviewStep } from '@/features/vm-provisioning/steps/ReviewStep'
import { ApiError, api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { PreflightCheck } from '@/types/api'

interface SubmitProblem {
  message: string
  /** Blocking server-side preflight checks; nothing was created. */
  checks: PreflightCheck[]
}

function blockingChecks(error: unknown): PreflightCheck[] {
  if (!(error instanceof ApiError)) return []
  const details = error.details as { blocking_checks?: PreflightCheck[] } | undefined
  return Array.isArray(details?.blocking_checks) ? details.blocking_checks : []
}

function StepContent() {
  const { currentStep } = useWizard()
  switch (currentStep.key) {
    case 'location':
      return <LocationStep />
    case 'media':
      return <MediaStep />
    case 'configuration':
      return <ConfigurationStep />
    case 'credentials':
      return <CredentialsStep />
    case 'network':
      return <NetworkStep />
    case 'directory':
      return <DirectoryStep />
    default:
      return <ReviewStep />
  }
}

function WizardShell() {
  const wizard = useWizard()
  const navigate = useNavigate()
  const { hasPermission } = useAuth()
  const [submitting, setSubmitting] = useState(false)
  const [submitProblem, setSubmitProblem] = useState<SubmitProblem | null>(null)
  const [discardOpen, setDiscardOpen] = useState(false)
  const isReview = wizard.currentStep.key === 'review'
  const canSubmit = hasPermission('provisioning.submit')
  const nextStep = WIZARD_STEPS[wizard.currentIndex + 1]

  // The server runs the full preflight on submission and creates nothing
  // unless every blocking check passes; its findings are shown inline.
  async function handleProvision() {
    if (!wizard.validateAll()) return
    setSubmitProblem(null)
    setSubmitting(true)
    try {
      const idempotencyKey =
        globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
      const job = await api.submitJob(wizard.requestPayload(), idempotencyKey)
      wizard.reset()
      navigate(`/jobs/${job.id}`)
    } catch (error) {
      setSubmitProblem({
        message: error instanceof Error ? error.message : 'The deployment could not be submitted.',
        checks: blockingChecks(error),
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto max-w-[1320px] space-y-6">
      <header className="animate-panel-reveal flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-[#17201c] text-[#d8f06a] shadow-[0_10px_24px_rgba(23,32,28,0.16)]"><Server className="h-4 w-4" /></span>
            <p className="console-kicker">Virtual machine deployment</p>
          </div>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.035em] text-[#17201c]">Create a virtual machine</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[#68736d]">
            Windows Server is installed from an ISO and configured without anyone at the console. Each choice limits the inventory shown in the next step.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="neutral"><CheckCircle2 className="h-3 w-3" /> Draft saved</Badge>
          <Button type="button" variant="ghost" size="sm" onClick={() => setDiscardOpen(true)}>
            <RotateCcw className="h-3.5 w-3.5" /> Start over
          </Button>
        </div>
      </header>

      <div className="overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)]">
        <Stepper
          steps={WIZARD_STEPS.map(({ key, title }) => ({ key, title }))}
          currentIndex={wizard.currentIndex}
          errorKeys={Object.keys(wizard.errors).length > 0 ? [wizard.currentStep.key] : []}
          onStepClick={wizard.goTo}
        />
      </div>

      <form
        className="overflow-hidden rounded-3xl border border-[#ccd6cd] bg-[radial-gradient(circle_at_100%_0%,rgba(216,240,106,0.12),transparent_32%),#fafbf8] shadow-[0_2px_4px_rgba(23,32,28,0.04),0_20px_52px_rgba(23,79,64,0.09)]"
        onSubmit={(event) => {
          event.preventDefault()
          if (!isReview) wizard.next()
        }}
      >
        <div className="provisioning-stage min-h-[520px] p-5 sm:p-7 lg:p-9">
          <StepContent />

          {Object.keys(wizard.errors).length > 0 && (
            <div className="mt-6">
              <Alert tone="danger" title="Complete the highlighted information">
                <ul className="mt-1 list-inside list-disc space-y-1 text-xs">
                  {[...new Set(Object.values(wizard.errors))].map((message) => <li key={message}>{message}</li>)}
                </ul>
              </Alert>
            </div>
          )}

          {submitProblem && (
            <div className="mt-6">
              <Alert tone="danger" title="Deployment is not ready — nothing was created">
                {submitProblem.checks.length > 0 ? (
                  <ul className="mt-1 list-inside list-disc space-y-1 text-xs" aria-label="Blocking checks">
                    {submitProblem.checks.map((check) => (
                      <li key={`${check.code}-${check.label}`}>
                        <strong>{check.label}:</strong> {check.detail}
                      </li>
                    ))}
                  </ul>
                ) : submitProblem.message}
              </Alert>
            </div>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-[#d8e1d9] bg-[linear-gradient(135deg,#ffffff_0%,#f0f6f0_100%)] px-5 py-4 sm:px-7">
          <Button type="button" variant="secondary" onClick={wizard.back} disabled={wizard.currentIndex === 0}>
            <ArrowLeft className="h-4 w-4" /> Back
          </Button>

          <div className="hidden items-center gap-2 text-[11px] text-[#758079] md:flex">
            <ShieldCheck className="h-3.5 w-3.5 text-brand-700" />
            Dependent inventory is reset whenever its datacenter scope changes.
          </div>

          {isReview ? (
            <Button
              type="button"
              loading={submitting}
              disabled={!canSubmit}
              title={canSubmit ? undefined : 'Your role cannot create virtual machines.'}
              onClick={() => void handleProvision()}
            >
              <Sparkles className="h-4 w-4" />
              Create virtual machine
            </Button>
          ) : (
            <Button type="submit">
              Continue{nextStep ? ` to ${nextStep.title}` : ''} <ArrowRight className="h-4 w-4" />
            </Button>
          )}
        </footer>
      </form>

      <Dialog
        open={discardOpen}
        onClose={() => setDiscardOpen(false)}
        title="Start a new deployment?"
        footer={(
          <>
            <Button type="button" variant="secondary" onClick={() => setDiscardOpen(false)}>Keep draft</Button>
            <Button type="button" variant="danger" onClick={() => { wizard.reset(); setDiscardOpen(false) }}>Discard draft</Button>
          </>
        )}
      >
        <p className="text-sm leading-6 text-[#59635d]">Every selection in this deployment draft will be cleared. This cannot be undone.</p>
      </Dialog>
    </div>
  )
}

export function VmProvisioningPage() {
  return <WizardProvider><WizardShell /></WizardProvider>
}
