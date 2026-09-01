import { Network as NetworkIcon, Radar } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Alert, Badge, EmptyState, LoadingState } from '@/components/ui/feedback'
import { FormRow, Input, RadioGroup, Select } from '@/components/ui/form-controls'
import { useWizard } from '@/features/vm-provisioning/context'
import { useNetworks } from '@/features/vm-provisioning/hooks'
import { api } from '@/lib/api'
import { humanizeIdentifier } from '@/lib/utils'
import type { IpConflictReport } from '@/types/api'

export function NetworkStep() {
  const wizard = useWizard()
  const data = wizard.data
  const networks = useNetworks(data.vcenter_id, data.datacenter_id)
  const [conflictReport, setConflictReport] = useState<IpConflictReport | null>(null)

  useEffect(() => {
    if (
      networks.isSuccess
      && data.network_id
      && !networks.data.some((network) => network.id === data.network_id)
    ) {
      wizard.update({ network_id: '' })
    }
  }, [data.network_id, networks.data, networks.isSuccess, wizard.update])

  const ipCheck = useMutation({
    mutationFn: () => {
      const prefix = /^\d{1,2}$/.test(data.prefix_input.trim()) ? Number(data.prefix_input.trim()) : 24
      return api.ipCheck(data.ip_address, prefix, data.vcenter_id || undefined)
    },
    onSuccess: setConflictReport,
  })

  return (
    <section aria-label="Network configuration" className="space-y-5">
      <header>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-700">Connectivity</p>
            <h2>Virtual network</h2>
            <p>
              {data.source_type === 'blank'
                ? 'Attach the virtual adapter now. Guest addressing is configured after OS installation.'
                : 'Attach a port group and define guest addressing applied through VMware Tools.'}
            </p>
          </div>
          <Badge tone="neutral">{data.source_type === 'blank' ? 'Adapter only' : 'Guest configuration'}</Badge>
        </div>
      </header>

      {networks.isError && (
        <Alert tone="danger" title="Network inventory unavailable">
          <span>{networks.error instanceof Error ? networks.error.message : 'Networks could not be loaded.'}</span>{' '}
          <Button type="button" size="sm" variant="secondary" onClick={() => void networks.refetch()}>Retry</Button>
        </Alert>
      )}

      {networks.isLoading && (
        <LoadingState
          title="Loading datacenter networks"
          description="Discovering standard and distributed port groups in the selected location."
        />
      )}

      {!networks.isLoading && !networks.isError && (networks.data ?? []).length === 0 && (
        <EmptyState
          title="No networks are available in the selected datacenter."
          action={<Button type="button" size="sm" variant="secondary" onClick={() => void networks.refetch()}>Retry</Button>}
        />
      )}

      <div className="console-group">
        <div className="console-group-header">
          <div>
            <p className="console-group-title">Virtual adapter</p>
            <p className="console-group-description">Inventory is scoped to the selected vCenter and datacenter.</p>
          </div>
          <NetworkIcon className="h-4 w-4 text-slate-400" aria-hidden />
        </div>
        <div className="console-group-body grid grid-cols-1 gap-x-4 md:grid-cols-[minmax(0,1fr)_220px]">
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
                    : 'Select port group'}
              </option>
              {(networks.data ?? []).map((network) => (
                <option key={network.id} value={network.id}>
                  {network.name} · {network.type === 'DISTRIBUTED_PORT_GROUP' ? 'Distributed' : 'Standard'}
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
              <option value="VMXNET3">VMXNET3 · Recommended</option>
              <option value="E1000E">E1000E · Legacy</option>
            </Select>
          </FormRow>
        </div>
      </div>

      {data.source_type === 'blank' ? (
        <Alert tone="info" title="No guest addressing will be submitted">
          The selected port group is attached to the powered-off VM. Configure IP addressing after installing the OS.
        </Alert>
      ) : (
        <div className="console-group">
          <div className="console-group-header">
            <div>
              <p className="console-group-title">Guest IPv4 configuration</p>
              <p className="console-group-description">Applied after VMware Tools becomes available.</p>
            </div>
          </div>
          <div className="console-group-body space-y-4">
            <FormRow label="Addressing policy" required className="mb-0">
              <RadioGroup
                name="ip-mode"
                columns={2}
                value={data.ip_mode}
                onChange={(value) => { wizard.update({ ip_mode: value }); setConflictReport(null) }}
                options={[
                  { value: 'DHCP', label: 'DHCP', description: 'Address assigned by the connected network.' },
                  { value: 'STATIC', label: 'Static IPv4', description: 'Apply an explicit address and DNS configuration.' },
                ]}
              />
            </FormRow>

            {data.ip_mode === 'STATIC' && (
              <>
                <div className="grid grid-cols-1 gap-x-4 md:grid-cols-3">
                  <FormRow label="IP address" htmlFor="ip-address" required error={wizard.errors.ip_address}>
                    <Input id="ip-address" inputMode="numeric" placeholder="10.20.30.45" className="font-mono" value={data.ip_address} onChange={(event) => { wizard.update({ ip_address: event.target.value }); setConflictReport(null) }} />
                  </FormRow>
                  <FormRow label="Subnet mask / prefix" htmlFor="ip-prefix" required hint="Example: 24 or 255.255.255.0" error={wizard.errors.prefix_input}>
                    <Input id="ip-prefix" className="font-mono" placeholder="24" value={data.prefix_input} onChange={(event) => wizard.update({ prefix_input: event.target.value })} />
                  </FormRow>
                  <FormRow label="Default gateway" htmlFor="ip-gateway" required error={wizard.errors.gateway}>
                    <Input id="ip-gateway" inputMode="numeric" placeholder="10.20.30.1" className="font-mono" value={data.gateway} onChange={(event) => wizard.update({ gateway: event.target.value })} />
                  </FormRow>
                  <FormRow label="Primary DNS" htmlFor="dns-primary" required error={wizard.errors.dns_primary}>
                    <Input id="dns-primary" inputMode="numeric" placeholder="10.20.1.10" className="font-mono" value={data.dns_primary} onChange={(event) => wizard.update({ dns_primary: event.target.value })} />
                  </FormRow>
                  <FormRow label="Secondary DNS" htmlFor="dns-secondary">
                    <Input id="dns-secondary" inputMode="numeric" placeholder="10.20.1.11" className="font-mono" value={data.dns_secondary} onChange={(event) => wizard.update({ dns_secondary: event.target.value })} />
                  </FormRow>
                  <FormRow label="Additional DNS" htmlFor="dns-extra" hint="Separate entries with spaces or commas.">
                    <Input id="dns-extra" className="font-mono" value={data.dns_extra} onChange={(event) => wizard.update({ dns_extra: event.target.value })} />
                  </FormRow>
                </div>

                <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Radar className="h-4 w-4 text-slate-500" />
                      <div>
                        <p className="text-xs font-semibold text-slate-800">Address conflict check</p>
                        <p className="text-[11px] text-slate-500">Queries ICMP, DNS, and vCenter inventory.</p>
                      </div>
                    </div>
                    <Button type="button" size="sm" variant="secondary" loading={ipCheck.isPending} disabled={!data.ip_address} onClick={() => ipCheck.mutate()}>
                      Check address
                    </Button>
                  </div>
                  {ipCheck.isError && <div className="mt-2"><Alert tone="danger">The conflict check could not be completed.</Alert></div>}
                  {conflictReport && (
                    <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
                      {conflictReport.providers.map((provider) => (
                        <div key={provider.provider} className="rounded border border-slate-200 bg-white px-2.5 py-2 text-xs">
                          <Badge tone={provider.status === 'CONFLICT_DETECTED' ? 'danger' : provider.status === 'NO_CONFLICT' ? 'success' : 'neutral'}>
                            {humanizeIdentifier(provider.status)}
                          </Badge>
                          <p className="mt-1 font-semibold text-slate-700">{humanizeIdentifier(provider.provider)}</p>
                          <p className="mt-0.5 text-[11px] text-slate-500">{provider.detail}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
