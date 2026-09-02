import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Clock3,
  Code2,
  FolderLock,
  RotateCcw,
  Save,
  ShieldCheck,
  Tags,
  UsersRound,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { FormRow, Input, Textarea } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { api } from '@/lib/api'
import { displayValue } from '@/lib/utils'
import type { PlatformSettingsOut } from '@/types/api'

type TimeoutKey = keyof PlatformSettingsOut['default_timeouts']

interface SettingsValidation {
  timeoutErrors: Record<TimeoutKey, string | null>
  isValid: boolean
}

function timeoutError(value: number, max: number): string | null {
  if (!Number.isFinite(value) || !Number.isInteger(value)) return `Enter a whole number from 1 to ${max} minutes.`
  if (value < 1 || value > max) return `Enter a value from 1 to ${max} minutes.`
  return null
}

export function validatePlatformSettings(value: PlatformSettingsOut): SettingsValidation {
  const timeoutErrors: SettingsValidation['timeoutErrors'] = {
    clone_minutes: timeoutError(value.default_timeouts.clone_minutes, 240),
    vmware_tools_minutes: timeoutError(value.default_timeouts.vmware_tools_minutes, 120),
    network_configuration_minutes: timeoutError(value.default_timeouts.network_configuration_minutes, 60),
    guest_operations_minutes: timeoutError(value.default_timeouts.guest_operations_minutes, 120),
  }
  return {
    timeoutErrors,
    isValid: Object.values(timeoutErrors).every((error) => !error),
  }
}

function SettingsSection({
  icon: Icon,
  eyebrow,
  title,
  description,
  children,
  aside,
}: {
  icon: LucideIcon
  eyebrow: string
  title: string
  description: string
  children: ReactNode
  aside?: ReactNode
}) {
  return (
    <section className="animate-panel-reveal overflow-hidden rounded-2xl border border-[#d8ddd7] bg-white shadow-[var(--ui-shadow)]">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-[#dce4dd] bg-[linear-gradient(135deg,#fbfcf9_0%,#eef5ef_100%)] px-5 py-4 sm:px-6">
        <div className="flex items-start gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-700 text-white shadow-sm"><Icon className="h-[18px] w-[18px]" /></span>
          <div>
            <p className="text-[9px] font-bold uppercase tracking-[0.11em] text-brand-700">{eyebrow}</p>
            <h2 className="mt-1 text-sm font-semibold tracking-[-0.01em] text-[#202923]">{title}</h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-[#6b756f]">{description}</p>
          </div>
        </div>
        {aside}
      </header>
      <div className="px-5 py-5 sm:px-6">{children}</div>
    </section>
  )
}

function TimeoutField({
  id,
  label,
  description,
  value,
  max,
  error,
  onChange,
}: {
  id: string
  label: string
  description: string
  value: number
  max: number
  error: string | null
  onChange: (value: number) => void
}) {
  const descriptionId = `${id}-description`
  const errorId = `${id}-error`
  return (
    <div className="rounded-2xl border border-[#dfe5df] bg-[#f9fbf8] p-4 transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-brand-200 hover:shadow-[0_10px_28px_rgba(23,79,64,0.07)]">
      <label className="text-xs font-semibold text-[#26312b]" htmlFor={id}>{label}</label>
      <p id={descriptionId} className="mt-1 min-h-10 text-[11px] leading-5 text-[#758079]">{description}</p>
      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
        <Input
          id={id}
          type="number"
          min={1}
          max={max}
          value={Number.isFinite(value) ? value : ''}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${descriptionId} ${errorId}` : descriptionId}
          onChange={(event) => onChange(event.target.value === '' ? Number.NaN : Number(event.target.value))}
        />
        <span className="rounded-lg border border-[#d7ded7] bg-white px-2.5 py-2 text-[10px] font-semibold text-[#657069]">minutes</span>
      </div>
      {error ? <p id={errorId} className="mt-2 text-[10px] font-medium text-red-700" role="alert">{error}</p> : <p className="mt-2 text-[10px] text-[#87908a]">Allowed range: 1–{max} minutes</p>}
    </div>
  )
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['admin-settings'], queryFn: () => api.admin.settings() })
  const roles = useQuery({ queryKey: ['admin-roles'], queryFn: () => api.admin.roles() })

  const [draft, setDraft] = useState<PlatformSettingsOut | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (settings.data && draft === null) setDraft(structuredClone(settings.data))
  }, [settings.data, draft])

  const save = useMutation({
    mutationFn: (payload: PlatformSettingsOut) =>
      api.admin.updateSettings({
        vm_name_policy_regex: payload.vm_name_policy_regex,
        allowed_installer_roots: payload.allowed_installer_roots
          .map((root) => root.trim())
          .filter(Boolean),
        default_timeouts: payload.default_timeouts,
        environment_label: payload.environment_label,
      }),
    onMutate: () => setSaved(false),
    onSuccess: (updated) => {
      setDraft(structuredClone(updated))
      setSaved(true)
      queryClient.setQueryData(['admin-settings'], updated)
      void queryClient.invalidateQueries({ queryKey: ['admin-settings'] })
    },
  })

  if (settings.isLoading) return <LoadingState title="Loading platform policy" description="Retrieving provisioning controls and execution defaults." />
  if (settings.isError) {
    return <Alert tone="danger" title="Platform settings unavailable">The settings API could not be reached. <Button size="sm" variant="secondary" onClick={() => void settings.refetch()}>Retry</Button></Alert>
  }
  if (!draft) return <LoadingState title="Preparing platform policy" description="Building an editable settings workspace." />

  const isDirty = JSON.stringify(draft) !== JSON.stringify(settings.data)
  const installerRootCount = draft.allowed_installer_roots.filter((root) => root.trim()).length
  const validation = validatePlatformSettings(draft)

  function resetDraft() {
    if (!settings.data) return
    setDraft(structuredClone(settings.data))
    setSaved(false)
  }

  return (
    <div className="max-w-6xl space-y-6">
      <PageHeader
        eyebrow="Platform administration"
        title="Platform settings"
        description="Set the operator-facing identity, validation guardrails, approved software locations, and default execution limits."
        meta={<><span>Changes apply to new validation and provisioning requests</span><span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5 text-brand-700" /> Running jobs keep their submitted settings</span></>}
        actions={(
          <>
            {saved && !isDirty && !save.isPending && <Badge tone="success"><ShieldCheck className="h-3 w-3" /> Saved</Badge>}
            <Button size="sm" variant="secondary" disabled={!isDirty || save.isPending} onClick={resetDraft}><RotateCcw className="h-3.5 w-3.5" /> Discard changes</Button>
            <Button size="sm" disabled={!isDirty || !validation.isValid} loading={save.isPending} onClick={() => save.mutate(draft)}><Save className="h-3.5 w-3.5" /> Save changes</Button>
          </>
        )}
      />

      <section className="animate-panel-reveal overflow-hidden rounded-2xl border border-[#285f50] bg-[#173f34] text-white shadow-[0_18px_44px_rgba(23,63,52,0.18)]" aria-label="Settings change scope">
        <div className="relative flex flex-wrap items-center justify-between gap-4 px-5 py-5 sm:px-6">
          <span className="pointer-events-none absolute -right-10 -top-20 h-40 w-40 rounded-full bg-[#d8f06a]/10 blur-2xl" aria-hidden />
          <div className="relative flex items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#d8f06a] text-[#173f34]"><ShieldCheck className="h-5 w-5" /></span>
            <div><h2 className="text-sm font-semibold">Safe change boundary</h2><p className="mt-1 max-w-2xl text-xs leading-5 text-white/65">Review and save these defaults together. Existing jobs are not rewritten, and unsaved edits can be discarded at any time.</p></div>
          </div>
          <Badge className={!validation.isValid ? 'bg-amber-100 text-amber-900 ring-amber-200' : isDirty ? 'bg-[#d8f06a] text-[#173f34] ring-[#d8f06a]' : 'bg-white/10 text-white ring-white/15'}>{!validation.isValid ? 'Review invalid values' : isDirty ? 'Unsaved changes' : 'Settings up to date'}</Badge>
        </div>
      </section>

      {save.isError && (
        <Alert tone="danger" title="Settings were not saved">
          {save.error instanceof Error ? save.error.message : 'No changes were applied. Review the values and try again.'}
        </Alert>
      )}
      {!validation.isValid && <Alert tone="warning" title="Review the highlighted settings">Save is disabled until all operation time limits contain valid values.</Alert>}

      <SettingsSection
        icon={Tags}
        eyebrow="Workspace identity"
        title="Environment label"
        description="Give operators a short, visible reminder of the environment they are working in."
        aside={<Badge tone="info">Header display</Badge>}
      >
        <div className="max-w-2xl">
          <FormRow label="Workspace label" htmlFor="setting-label" hint="Shown beside the InfraOps logo. Keep it short and specific, such as Production, Staging, or Lab.">
            <Input id="setting-label" maxLength={40} placeholder="Production" value={draft.environment_label} onChange={(event) => setDraft({ ...draft, environment_label: event.target.value })} />
          </FormRow>
          <p className="text-right text-[10px] tabular-nums text-[#87908a]">{draft.environment_label.length} of 40 characters</p>
        </div>
      </SettingsSection>

      <SettingsSection
        icon={FolderLock}
        eyebrow="Provisioning safeguards"
        title="Names and approved software locations"
        description="Validate virtual machine names and restrict installer access before provisioning work begins."
        aside={<Badge tone="success">{installerRootCount} approved location{installerRootCount === 1 ? '' : 's'}</Badge>}
      >
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="rounded-2xl border border-[#dfe5df] bg-[#f9fbf8] p-4 sm:p-5">
            <div className="mb-4 flex items-start gap-3"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white text-brand-700 ring-1 ring-inset ring-brand-100"><Code2 className="h-4 w-4" /></span><div><h3 className="text-xs font-semibold text-[#26312b]">Virtual machine naming rule</h3><p className="mt-1 text-[11px] leading-5 text-[#758079]">An optional Python regular expression checked before a request is accepted. Leave this blank to allow any name.</p></div></div>
            <FormRow label="Naming rule (advanced)" htmlFor="setting-policy" hint="Validated by the platform when you save. Example: ^[A-Z]{3,10}-[A-Z0-9]{2,8}-\d{3}$" className="mb-0">
              <Input id="setting-policy" className="font-mono text-xs" placeholder="Leave blank to disable" spellCheck={false} value={draft.vm_name_policy_regex} onChange={(event) => setDraft({ ...draft, vm_name_policy_regex: event.target.value })} />
            </FormRow>
          </div>

          <div className="rounded-2xl border border-[#dfe5df] bg-[#f9fbf8] p-4 sm:p-5">
            <div className="mb-4 flex items-start gap-3"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white text-brand-700 ring-1 ring-inset ring-brand-100"><FolderLock className="h-4 w-4" /></span><div><h3 className="text-xs font-semibold text-[#26312b]">Approved software locations</h3><p className="mt-1 text-[11px] leading-5 text-[#758079]">Installer files outside these Windows network roots are blocked during validation.</p></div></div>
            <FormRow label="Network repository roots" htmlFor="setting-roots" hint="Enter one UNC root per line, for example \\fileserver\software." className="mb-0">
              <Textarea id="setting-roots" rows={5} className="font-mono text-xs" placeholder={'\\\\fileserver\\software'} value={draft.allowed_installer_roots.join('\n')} onChange={(event) => setDraft({ ...draft, allowed_installer_roots: event.target.value.split('\n') })} />
            </FormRow>
          </div>
        </div>
      </SettingsSection>

      <SettingsSection
        icon={Clock3}
        eyebrow="Execution limits"
        title="Default operation time limits"
        description="Choose how long a provisioning stage may run before InfraOps marks it as timed out. Every value is measured in minutes."
        aside={<Badge tone="info">Minutes</Badge>}
      >
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <TimeoutField id="timeout-clone" label="Clone virtual machine" description="Maximum time to copy the selected template or source image." value={draft.default_timeouts.clone_minutes} max={240} error={validation.timeoutErrors.clone_minutes} onChange={(value) => setDraft({ ...draft, default_timeouts: { ...draft.default_timeouts, clone_minutes: value } })} />
          <TimeoutField id="timeout-tools" label="Wait for VMware Tools" description="Maximum time to wait for guest readiness after power-on." value={draft.default_timeouts.vmware_tools_minutes} max={120} error={validation.timeoutErrors.vmware_tools_minutes} onChange={(value) => setDraft({ ...draft, default_timeouts: { ...draft.default_timeouts, vmware_tools_minutes: value } })} />
          <TimeoutField id="timeout-network" label="Configure guest network" description="Maximum time to apply and verify network settings inside the VM." value={draft.default_timeouts.network_configuration_minutes} max={60} error={validation.timeoutErrors.network_configuration_minutes} onChange={(value) => setDraft({ ...draft, default_timeouts: { ...draft.default_timeouts, network_configuration_minutes: value } })} />
          <TimeoutField id="timeout-guest" label="Run guest operations" description="Maximum time for certificates, installers, and other guest tasks." value={draft.default_timeouts.guest_operations_minutes} max={120} error={validation.timeoutErrors.guest_operations_minutes} onChange={(value) => setDraft({ ...draft, default_timeouts: { ...draft.default_timeouts, guest_operations_minutes: value } })} />
        </div>
      </SettingsSection>

      <SettingsSection
        icon={UsersRound}
        eyebrow="Read-only reference"
        title="Platform access roles"
        description="Review the roles returned by the identity service. Role membership is managed outside this settings page."
        aside={<Badge>{roles.data?.length ?? 0} role{roles.data?.length === 1 ? '' : 's'}</Badge>}
      >
        {roles.isLoading ? (
          <LoadingState title="Loading roles" description="Retrieving the platform's access roles." />
        ) : roles.isError ? (
          <Alert tone="danger" title="Roles could not be loaded">The role catalog is unavailable. <Button size="sm" variant="secondary" onClick={() => void roles.refetch()}>Retry</Button></Alert>
        ) : (roles.data ?? []).length === 0 ? (
          <EmptyState title="No roles are configured" description="No platform access roles were returned." />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(roles.data ?? []).map((role) => (
              <article key={role.id} className="animate-row-enter rounded-2xl border border-[#dfe5df] bg-[#f9fbf8] p-4 transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-brand-200 hover:shadow-[0_10px_28px_rgba(23,79,64,0.07)]">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-white text-brand-700 ring-1 ring-inset ring-brand-100"><UsersRound className="h-4 w-4" /></span>
                <h3 className="mt-3 text-sm font-semibold text-[#26312b]">{role.name}</h3>
                <p className="mt-1.5 text-[11px] leading-5 text-[#758079]">{displayValue(role.description, 'No role description is available.')}</p>
              </article>
            ))}
          </div>
        )}
      </SettingsSection>
    </div>
  )
}
