import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { FormRow, Input, RadioGroup, Select } from '@/components/ui/form-controls'
import { api } from '@/lib/api'
import { useWizard } from '@/features/vm-provisioning/context'
import { useNetworks } from '@/features/vm-provisioning/hooks'
import type { IpConflictReport } from '@/types/api'

export function NetworkStep() {
  const wizard = useWizard()
  const data = wizard.data
  const networks = useNetworks(data.vcenter_id, data.datacenter_id)
  const [conflictReport, setConflictReport] = useState<IpConflictReport | null>(null)

  const ipCheck = useMutation({
    mutationFn: () => {
      const prefix = /^\d{1,2}$/.test(data.prefix_input.trim())
        ? Number(data.prefix_input.trim())
        : undefined
      return api.ipCheck(data.ip_address, prefix ?? 24, data.vcenter_id || undefined)
    },
    onSuccess: (report) => setConflictReport(report),
  })

  return (
    <section aria-label="Network configuration" className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold text-slate-900">Network</h2>
        <p className="mt-1 text-xs text-slate-500">
          {data.source_type === 'blank'
            ? 'Attach the virtual adapter now; configure addressing after the operating system is installed.'
            : 'Attach a port group and configure guest addressing through VMware Tools.'}
        </p>
      </header>

      {networks.isError && (
        <Alert tone="danger" title="Network retrieval failed">
          <span>{networks.error instanceof Error ? networks.error.message : 'Networks could not be loaded.'}</span>{' '}
          <Button type="button" size="sm" variant="secondary" onClick={() => void networks.refetch()}>
            Retry
          </Button>
        </Alert>
      )}

      {!networks.isLoading && !networks.isError && (networks.data ?? []).length === 0 && (
        <EmptyState
          title="No networks are available in the selected datacenter."
          action={<Button type="button" size="sm" variant="secondary" onClick={() => void networks.refetch()}>Retry</Button>}
        />
      )}

      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <FormRow label="Network / port group" htmlFor="network" required error={wizard.errors.network_id}>
          <Select
            id="network"
            value={data.network_id}
            onChange={(event) => wizard.update({ network_id: event.target.value })}
            disabled={!data.datacenter_id || networks.isLoading || networks.isError || (networks.data ?? []).length === 0}
          >
            <option value="">
              {!data.datacenter_id
                ? 'Select infrastructure first'
                : networks.isLoading
                  ? 'Loading networks…'
                  : 'Select a network'}
            </option>
            {(networks.data ?? []).map((network) => (
              <option key={network.id} value={network.id}>
                {network.name} {network.type === 'DISTRIBUTED_PORT_GROUP' ? '(distributed)' : '(standard)'}
              </option>
            ))}
          </Select>
        </FormRow>

        <FormRow label="Adapter type" htmlFor="adapter-type">
          <Select
            id="adapter-type"
            value={data.adapter_type}
            onChange={(event) => wizard.update({ adapter_type: event.target.value as 'VMXNET3' | 'E1000E' })}
          >
            <option value="VMXNET3">VMXNET3 (recommended)</option>
            <option value="E1000E">E1000E (legacy)</option>
          </Select>
        </FormRow>
      </div>

      {data.source_type === 'blank' ? (
        <Alert tone="info">Guest IP configuration is not submitted for a blank VM.</Alert>
      ) : (
        <>
          <FormRow label="IP configuration" required>
            <RadioGroup
              name="ip-mode"
              columns={2}
              value={data.ip_mode}
              onChange={(value) => wizard.update({ ip_mode: value })}
              options={[
                { value: 'DHCP', label: 'DHCP', description: 'Address assigned by the network.' },
                { value: 'STATIC', label: 'Static', description: 'Configure a fixed address below.' },
              ]}
            />
          </FormRow>

          {data.ip_mode === 'STATIC' && (
            <>
              <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-3">
                <FormRow label="IP address" htmlFor="ip-address" required error={wizard.errors.ip_address}>
                  <Input id="ip-address" inputMode="numeric" placeholder="10.20.30.45" className="font-mono"
                    value={data.ip_address} onChange={(event) => wizard.update({ ip_address: event.target.value })} />
                </FormRow>
                <FormRow label="Subnet mask / prefix" htmlFor="ip-prefix" required hint="255.255.255.0 or 24"
                  error={wizard.errors.prefix_input}>
                  <Input id="ip-prefix" className="font-mono" placeholder="24" value={data.prefix_input}
                    onChange={(event) => wizard.update({ prefix_input: event.target.value })} />
                </FormRow>
                <FormRow label="Default gateway" htmlFor="ip-gateway" required error={wizard.errors.gateway}>
                  <Input id="ip-gateway" inputMode="numeric" placeholder="10.20.30.1" className="font-mono"
                    value={data.gateway} onChange={(event) => wizard.update({ gateway: event.target.value })} />
                </FormRow>
                <FormRow label="Primary DNS" htmlFor="dns-primary" required error={wizard.errors.dns_primary}>
                  <Input id="dns-primary" inputMode="numeric" placeholder="10.20.1.10" className="font-mono"
                    value={data.dns_primary} onChange={(event) => wizard.update({ dns_primary: event.target.value })} />
                </FormRow>
                <FormRow label="Secondary DNS" htmlFor="dns-secondary">
                  <Input id="dns-secondary" inputMode="numeric" placeholder="10.20.1.11" className="font-mono"
                    value={data.dns_secondary} onChange={(event) => wizard.update({ dns_secondary: event.target.value })} />
                </FormRow>
                <FormRow label="Additional DNS servers" htmlFor="dns-extra" hint="Separate with spaces or commas.">
                  <Input id="dns-extra" className="font-mono" value={data.dns_extra}
                    onChange={(event) => wizard.update({ dns_extra: event.target.value })} />
                </FormRow>
              </div>

              <div className="rounded-md border border-slate-200 p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium text-slate-700">IP conflict validation</p>
                  <Button type="button" size="sm" variant="secondary" loading={ipCheck.isPending}
                    disabled={!data.ip_address} onClick={() => ipCheck.mutate()}>
                    Check address now
                  </Button>
                </div>
                {ipCheck.isPending && (
                  <div className="mt-2 flex items-center gap-2 text-xs text-slate-400">
                    <Spinner className="h-3.5 w-3.5" /> Probing ICMP, DNS and inventory sources…
                  </div>
                )}
                {ipCheck.isError && (
                  <div className="mt-2"><Alert tone="danger">The IP conflict check could not be completed.</Alert></div>
                )}
                {conflictReport && (
                  <div className="mt-2 space-y-1.5">
                    {conflictReport.providers.map((provider) => (
                      <div key={provider.provider} className="flex items-center gap-2 text-xs">
                        <Badge tone={provider.status === 'CONFLICT_DETECTED' ? 'danger' : provider.status === 'NO_CONFLICT' ? 'success' : 'neutral'}>
                          {provider.status.replaceAll('_', ' ')}
                        </Badge>
                        <span className="font-medium text-slate-700">{provider.provider}</span>
                        <span className="text-slate-500">{provider.detail}</span>
                      </div>
                    ))}
                    <Alert tone={conflictReport.conflict_detected ? 'danger' : 'info'}>
                      {conflictReport.confidence_note}
                    </Alert>
                  </div>
                )}
              </div>
            </>
          )}
        </>
      )}
    </section>
  )
}
