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

const STORAGE_KEY = 'infraops.provisioning-draft.v2'

export interface WizardStepDefinition {
  key: string
  title: string
}

export const WIZARD_STEPS: WizardStepDefinition[] = [
  { key: 'deployment', title: 'Deployment type' },
  { key: 'location', title: 'Location' },
  { key: 'media', title: 'Source' },
  { key: 'configuration', title: 'Configuration' },
  { key: 'network', title: 'Network' },
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
    const raw = localStorage.getItem(STORAGE_KEY)
    const draft = raw ? { ...fresh, ...(JSON.parse(raw) as WizardData) } : fresh
    const query = new URLSearchParams(window.location.search)
    const templateId = query.get('template_id')
    if (templateId) {
      const vcenterId = query.get('vcenter_id') ?? draft.vcenter_id
      const datacenterId = query.get('datacenter_id') ?? draft.datacenter_id
      const targetChanged = vcenterId !== draft.vcenter_id || datacenterId !== draft.datacenter_id
      const sourceChanged = draft.source_type !== 'template'
      return {
        ...draft,
        source_type: 'template',
        template_id: templateId,
        iso_id: null,
        vcenter_id: vcenterId,
        datacenter_id: datacenterId,
        ...(targetChanged
          ? {
              cluster_id: '',
              host_mode: 'auto' as const,
              host_id: null,
              resource_pool_id: null,
              datastore_id: null,
              network_id: '',
              disks: draft.disks.map((disk) => ({ ...disk, datastore_id: null })),
            }
          : {}),
        ...(sourceChanged
          ? {
              hostname: '',
              timezone: '',
              domain_join: { ...draft.domain_join, enabled: false },
              certificate_package_ids: [],
              application_ids: [],
            }
          : {}),
      }
    }
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
    setData((previous) => {
      let normalizedPatch = { ...patch }

      if (patch.source_type !== undefined && patch.source_type !== previous.source_type) {
        normalizedPatch = {
          ...normalizedPatch,
          template_id: '',
          iso_id: null,
          hostname: '',
          timezone: '',
          domain_join: { ...previous.domain_join, enabled: false },
          certificate_package_ids: [],
          application_ids: [],
        }
      }
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
          template_id: '',
          iso_id: null,
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
          template_id: '',
          iso_id: null,
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

      const next: WizardData = {
        ...previous,
        ...normalizedPatch,
        ...(normalizedPatch.source_type === 'blank'
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
