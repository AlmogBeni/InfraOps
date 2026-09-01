/** Wizard state: draft persistence, step navigation and validation gating. */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

import {
  buildRequest,
  initialWizardData,
  stepSchemas,
  validateStep,
  type StepKey,
  type WizardData,
} from '@/features/vm-provisioning/schema'

const STORAGE_KEY = 'infraops.provisioning-draft'

export interface WizardStepDefinition {
  key: string
  title: string
}

export const WIZARD_STEPS: WizardStepDefinition[] = [
  { key: 'source', title: 'Source' },
  { key: 'infrastructure', title: 'Infrastructure' },
  { key: 'compute', title: 'Compute' },
  { key: 'storage', title: 'Storage' },
  { key: 'network', title: 'Network' },
  { key: 'os', title: 'Operating System' },
  { key: 'certificates', title: 'Certificates' },
  { key: 'applications', title: 'Applications' },
  { key: 'review', title: 'Review' },
]

interface WizardContextValue {
  data: WizardData
  update: (patch: Partial<WizardData>) => void
  currentIndex: number
  currentStep: WizardStepDefinition
  goTo: (index: number) => void
  next: () => boolean
  back: () => void
  errors: Record<string, string>
  setErrors: (errors: Record<string, string>) => void
  reset: () => void
  requestPayload: () => ReturnType<typeof buildRequest>
}

const WizardContext = createContext<WizardContextValue | null>(null)

function loadDraft(): WizardData {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...initialWizardData(), ...(JSON.parse(raw) as WizardData) }
  } catch {
    /* corrupted draft — start fresh */
  }
  return initialWizardData()
}

export function WizardProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<WizardData>(loadDraft)
  const [currentIndex, setCurrentIndex] = useState(0)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const update = useCallback((patch: Partial<WizardData>) => {
    setData((previous) => {
      const next = {
        ...previous,
        ...patch,
        ...(patch.source_type === 'blank'
          ? {
              template_id: '',
              hostname: '',
              timezone: '',
              domain_join: { ...previous.domain_join, enabled: false },
              certificate_package_ids: [],
              application_ids: [],
            }
          : {}),
      }
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {
        /* storage full/unavailable — non-fatal */
      }
      return next
    })
  }, [])

  const goTo = useCallback(
    (index: number) => {
      setCurrentIndex(Math.max(0, Math.min(WIZARD_STEPS.length - 1, index)))
      setErrors({})
    },
    [],
  )

  const next = useCallback(() => {
    const step = WIZARD_STEPS[currentIndex]
    if (step.key in stepSchemas) {
      const found = validateStep(step.key as StepKey, data)
      if (Object.keys(found).length > 0) {
        setErrors(found)
        return false
      }
    }
    setCurrentIndex((index) => Math.min(WIZARD_STEPS.length - 1, index + 1))
    setErrors({})
    return true
  }, [currentIndex, data])

  const back = useCallback(() => {
    setCurrentIndex((index) => Math.max(0, index - 1))
    setErrors({})
  }, [])

  const reset = useCallback(() => {
    const fresh = initialWizardData()
    setData(fresh)
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
    setCurrentIndex(0)
    setErrors({})
  }, [])

  const requestPayload = useCallback(() => buildRequest(data), [data])

  const value = useMemo(
    () => ({
      data,
      update,
      currentIndex,
      currentStep: WIZARD_STEPS[currentIndex],
      goTo,
      next,
      back,
      errors,
      setErrors,
      reset,
      requestPayload,
    }),
    [data, update, currentIndex, goTo, next, back, errors, reset, requestPayload],
  )

  return <WizardContext.Provider value={value}>{children}</WizardContext.Provider>
}

export function useWizard(): WizardContextValue {
  const context = useContext(WizardContext)
  if (!context) throw new Error('useWizard must be used inside <WizardProvider>')
  return context
}
