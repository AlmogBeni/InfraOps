/** Wizard state: draft persistence, step navigation and validation gating. */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

import {
  buildRequest,
  initialWizardData,
  isDesktopExperienceEdition,
  stepSchemas,
  validateStep,
  type StepKey,
  type WizardData,
} from '@/features/vm-provisioning/schema'

const STORAGE_KEY = 'infraops.provisioning-draft.v3'
// Drafts of earlier wizard versions describe VM sources that no longer exist.
const RETIRED_STORAGE_KEYS = ['infraops.provisioning-draft.v2']

export interface WizardStepDefinition {
  key: string
  title: string
}

export const WIZARD_STEPS: WizardStepDefinition[] = [
  { key: 'location', title: 'Location' },
  { key: 'media', title: 'Windows media' },
  { key: 'configuration', title: 'Configuration' },
  { key: 'credentials', title: 'Administrator' },
  { key: 'network', title: 'Network' },
  { key: 'directory', title: 'Domain join' },
  { key: 'review', title: 'Review' },
]

interface WizardContextValue {
  data: WizardData
  update: (patch: Partial<WizardData>) => void
  currentIndex: number
  currentStep: WizardStepDefinition
  goTo: (index: number) => void
  next: () => boolean
  validateAll: () => boolean
  back: () => void
  errors: Record<string, string>
  setErrors: (errors: Record<string, string>) => void
  reset: () => void
  requestPayload: () => ReturnType<typeof buildRequest>
}

const WizardContext = createContext<WizardContextValue | null>(null)

function loadDraft(): WizardData {
  const fresh = initialWizardData()
  try {
    for (const key of RETIRED_STORAGE_KEYS) localStorage.removeItem(key)
    const raw = localStorage.getItem(STORAGE_KEY)
    const draft = raw ? { ...fresh, ...(JSON.parse(raw) as Partial<WizardData>) } : fresh
    // Drafts saved before editions replaced the free image index.
    if (!isDesktopExperienceEdition(draft.windows_image_index)) draft.windows_image_index = fresh.windows_image_index
    return draft
  } catch {
    /* corrupted draft — start fresh */
  }
  return fresh
}

export function WizardProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<WizardData>(loadDraft)
  const [currentIndex, setCurrentIndex] = useState(0)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const update = useCallback((patch: Partial<WizardData>) => {
    const changedFields = Object.keys(patch)
    setErrors((previous) => Object.fromEntries(
      Object.entries(previous).filter(([field]) => !changedFields.some(
        (changed) => field === changed || field.startsWith(`${changed}.`),
      )),
    ))
    setData((previous) => {
      let normalizedPatch = { ...patch }

      if (patch.vcenter_id !== undefined && patch.vcenter_id !== previous.vcenter_id) {
        normalizedPatch = {
          ...normalizedPatch,
          datacenter_id: '',
          cluster_id: '',
          host_mode: 'auto',
          host_id: null,
          resource_pool_id: null,
          datastore_id: null,
          network_id: '',
          iso_id: '',
        }
      }
      if (patch.datacenter_id !== undefined && patch.datacenter_id !== previous.datacenter_id) {
        normalizedPatch = {
          ...normalizedPatch,
          cluster_id: '',
          host_mode: 'auto',
          host_id: null,
          resource_pool_id: null,
          datastore_id: null,
          network_id: '',
          iso_id: '',
          disks: previous.disks.map((disk) => ({ ...disk, datastore_id: null })),
        }
      }
      if (patch.cluster_id !== undefined && patch.cluster_id !== previous.cluster_id) {
        normalizedPatch = {
          ...normalizedPatch,
          host_id: null,
          resource_pool_id: null,
          datastore_id: null,
          disks: previous.disks.map((disk) => ({ ...disk, datastore_id: null })),
        }
      }

      const next: WizardData = { ...previous, ...normalizedPatch }
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {
        /* storage full/unavailable — non-fatal */
      }
      return next
    })
  }, [])

  const goTo = useCallback((index: number) => {
    const target = Math.max(0, Math.min(WIZARD_STEPS.length - 1, index))
    if (target <= currentIndex) {
      setCurrentIndex(target)
      setErrors({})
      return
    }
    for (let stepIndex = 0; stepIndex < target; stepIndex += 1) {
      const step = WIZARD_STEPS[stepIndex]
      if (!(step.key in stepSchemas)) continue
      const found = validateStep(step.key as StepKey, data)
      if (Object.keys(found).length > 0) {
        setCurrentIndex(stepIndex)
        setErrors(found)
        return
      }
    }
    setCurrentIndex(target)
    setErrors({})
  }, [currentIndex, data])

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

  const validateAll = useCallback(() => {
    for (let stepIndex = 0; stepIndex < WIZARD_STEPS.length; stepIndex += 1) {
      const step = WIZARD_STEPS[stepIndex]
      if (!(step.key in stepSchemas)) continue
      const found = validateStep(step.key as StepKey, data)
      if (Object.keys(found).length > 0) {
        setCurrentIndex(stepIndex)
        setErrors(found)
        return false
      }
    }
    setErrors({})
    return true
  }, [data])

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
      validateAll,
      back,
      errors,
      setErrors,
      reset,
      requestPayload,
    }),
    [data, update, currentIndex, goTo, next, validateAll, back, errors, reset, requestPayload],
  )

  return <WizardContext.Provider value={value}>{children}</WizardContext.Provider>
}

export function useWizard(): WizardContextValue {
  const context = useContext(WizardContext)
  if (!context) throw new Error('useWizard must be used inside <WizardProvider>')
  return context
}
