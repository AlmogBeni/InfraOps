import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { FormRow, Input, Textarea } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import type { PlatformSettingsOut } from '@/types/api'

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
        allowed_installer_roots: payload.allowed_installer_roots,
        default_timeouts: payload.default_timeouts,
        environment_label: payload.environment_label,
      }),
    onMutate: () => setSaved(false),
    onSuccess: (updated) => {
      setDraft(structuredClone(updated))
      setSaved(true)
      void queryClient.invalidateQueries({ queryKey: ['admin-settings'] })
    },
  })

  if (settings.isLoading) {
    return <LoadingState title="Loading platform policy" description="Retrieving provisioning controls and execution defaults." />
  }
  if (settings.isError) {
    return (
      <Alert tone="danger" title="Platform settings unavailable">
        The settings API could not be reached.{' '}
        <Button size="sm" variant="secondary" onClick={() => void settings.refetch()}>Retry</Button>
      </Alert>
    )
  }
  if (!draft) return <LoadingState title="Preparing platform policy" description="Building an editable settings workspace." />
  const isDirty = JSON.stringify(draft) !== JSON.stringify(settings.data)

  return (
    <div className="max-w-5xl space-y-4">
      <PageHeader
        eyebrow="Platform administration"
        title="Policy and execution defaults"
        description="Set naming controls, repository boundaries, stage timeouts, and the operator-facing environment label."
        meta={<span>Changes apply to new validation and provisioning requests</span>}
        actions={
          <>
            {saved && !isDirty && !save.isPending && <Badge tone="success">Saved</Badge>}
            <Button
              size="sm"
              disabled={!isDirty}
              loading={save.isPending}
              onClick={() => save.mutate(draft)}
            >
              Save changes
            </Button>
          </>
        }
      />
      {save.isError && (
        <Alert tone="danger" title="Settings were not saved">
          Check the API response and retry the change.
        </Alert>
      )}
      <Card>
        <CardHeader><CardTitle>Naming & policy</CardTitle></CardHeader>
        <CardContent>
          <FormRow label="VM name policy (regex)" htmlFor="setting-policy"
                   hint="Empty disables the check. Example: ^[A-Z]{3,10}-[A-Z0-9]{2,8}-\d{3}$">
            <Input id="setting-policy" className="font-mono text-xs" value={draft.vm_name_policy_regex}
                   onChange={(event) => setDraft({ ...draft, vm_name_policy_regex: event.target.value })} />
          </FormRow>

          <FormRow label="Approved installer repository roots" htmlFor="setting-roots"
                   hint="One UNC root per line. Application provisioning is blocked when a path falls outside these roots.">
            <Textarea id="setting-roots" rows={3} className="font-mono text-xs"
                      value={draft.allowed_installer_roots.join('\n')}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          allowed_installer_roots: event.target.value.split('\n').map((line) => line.trim()).filter(Boolean),
                        })
                      } />
          </FormRow>

        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Default stage timeouts (minutes)</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          <FormRow label="VM clone" htmlFor="timeout-clone">
            <Input id="timeout-clone" type="number" min={1} max={240} value={draft.default_timeouts.clone_minutes}
                   onChange={(event) =>
                     setDraft({
                       ...draft,
                       default_timeouts: { ...draft.default_timeouts, clone_minutes: Number(event.target.value) },
                     })
                   } />
          </FormRow>
          <FormRow label="VMware Tools wait" htmlFor="timeout-tools">
            <Input id="timeout-tools" type="number" min={1} max={120} value={draft.default_timeouts.vmware_tools_minutes}
                   onChange={(event) =>
                     setDraft({
                       ...draft,
                       default_timeouts: { ...draft.default_timeouts, vmware_tools_minutes: Number(event.target.value) },
                     })
                   } />
          </FormRow>
          <FormRow label="Network configuration" htmlFor="timeout-network">
            <Input id="timeout-network" type="number" min={1} max={60} value={draft.default_timeouts.network_configuration_minutes}
                   onChange={(event) =>
                     setDraft({
                       ...draft,
                       default_timeouts: { ...draft.default_timeouts, network_configuration_minutes: Number(event.target.value) },
                     })
                   } />
          </FormRow>
          <FormRow label="Guest operations" htmlFor="timeout-guest">
            <Input id="timeout-guest" type="number" min={1} max={120} value={draft.default_timeouts.guest_operations_minutes}
                   onChange={(event) =>
                     setDraft({
                       ...draft,
                       default_timeouts: { ...draft.default_timeouts, guest_operations_minutes: Number(event.target.value) },
                     })
                   } />
          </FormRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Display</CardTitle></CardHeader>
        <CardContent>
          <FormRow label="Environment label" htmlFor="setting-label" hint="Shown next to the logo in the header.">
            <Input id="setting-label" maxLength={40} value={draft.environment_label}
                   onChange={(event) => setDraft({ ...draft, environment_label: event.target.value })} />
          </FormRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Roles</CardTitle></CardHeader>
        <CardContent className="p-0">
          {roles.isLoading ? (
            <LoadingState title="Loading roles" description="Retrieving the platform's access roles." />
          ) : roles.isError ? (
            <div className="p-4">
              <Alert tone="danger" title="Roles could not be loaded">
                The role catalog is unavailable.{' '}
                <Button size="sm" variant="secondary" onClick={() => void roles.refetch()}>Retry</Button>
              </Alert>
            </div>
          ) : (roles.data ?? []).length === 0 ? (
            <EmptyState title="No roles are configured" description="No platform access roles were returned." />
          ) : (
            <Table className="border-0">
              <thead>
                <tr><Th>Role</Th><Th>Description</Th></tr>
              </thead>
              <tbody>
                {(roles.data ?? []).map((role) => (
                  <Tr key={role.id}>
                    <Td className="font-medium text-slate-800">{role.name}</Td>
                    <Td className="text-slate-600">{role.description}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
