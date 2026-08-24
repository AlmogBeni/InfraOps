import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, Badge, Spinner } from '@/components/ui/feedback'
import { FormRow, Input, Textarea } from '@/components/ui/form-controls'
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
    onSuccess: () => {
      setSaved(true)
      void queryClient.invalidateQueries({ queryKey: ['admin-settings'] })
    },
  })

  if (settings.isLoading || !draft) {
    return (
      <div className="flex h-40 items-center justify-center"><Spinner /></div>
    )
  }

  return (
    <div className="max-w-3xl space-y-6">
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

      <div className="flex items-center gap-3">
        <Button loading={save.isPending} onClick={() => save.mutate(draft)}>Save settings</Button>
        {saved && !save.isPending && <Badge tone="success">Saved</Badge>}
        {save.isError && <Alert tone="danger">Save failed — please retry.</Alert>}
      </div>

      <Card>
        <CardHeader><CardTitle>Roles</CardTitle></CardHeader>
        <CardContent className="p-0">
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
        </CardContent>
      </Card>
    </div>
  )
}
