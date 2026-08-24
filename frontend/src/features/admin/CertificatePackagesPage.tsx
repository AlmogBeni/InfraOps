import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Alert, Badge, Spinner } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select, Textarea } from '@/components/ui/form-controls'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'

export function CertificatePackagesPage() {
  const queryClient = useQueryClient()
  const [packageDialogOpen, setPackageDialogOpen] = useState(false)
  const [packageName, setPackageName] = useState('')
  const [packageDescription, setPackageDescription] = useState('')
  const [certDialogFor, setCertDialogFor] = useState<string | null>(null)
  const [certForm, setCertForm] = useState({
    friendly_name: '',
    certificate_type: 'ROOT' as 'ROOT' | 'INTERMEDIATE',
    pem_body: '',
  })
  const [formError, setFormError] = useState<string | null>(null)

  const packages = useQuery({ queryKey: ['admin-packages'], queryFn: () => api.admin.packages() })

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ['admin-packages'] })
    void queryClient.invalidateQueries({ queryKey: ['certificate-packages'] })
  }

  const createPackage = useMutation({
    mutationFn: () =>
      api.admin.createPackage({ name: packageName, description: packageDescription }),
    onSuccess: () => {
      setPackageDialogOpen(false)
      setPackageName('')
      setPackageDescription('')
      invalidate()
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : 'Create failed.'),
  })

  const registerCertificate = useMutation({
    mutationFn: async () => {
      if (!certDialogFor) throw new Error('No package selected.')
      return api.admin.registerCertificate({
        package_id: certDialogFor,
        friendly_name: certForm.friendly_name,
        certificate_type: certForm.certificate_type,
        pem_body: certForm.pem_body,
      })
    },
    onSuccess: () => {
      setCertDialogFor(null)
      setCertForm({ friendly_name: '', certificate_type: 'ROOT', pem_body: '' })
      invalidate()
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : 'Registration failed.'),
  })

  const removeCertificate = useMutation({
    mutationFn: (id: string) => api.admin.deleteCertificate(id),
    onSuccess: invalidate,
  })

  const toggleCertificate = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.admin.updateCertificate(id, { enabled }),
    onSuccess: invalidate,
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          Packages group certificates that operators select during provisioning. Fingerprints and validity
          dates are computed server-side from the PEM body.
        </p>
        <Button onClick={() => setPackageDialogOpen(true)}>
          <Plus className="h-4 w-4" /> Add package
        </Button>
      </div>

      {packages.isLoading ? (
        <div className="flex h-40 items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <div className="space-y-4">
          {(packages.data ?? []).map((package_) => (
            <div key={package_.id} className="rounded-lg border border-slate-200 bg-white shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
                <div>
                  <p className="text-sm font-semibold text-slate-800">
                    {package_.name}{' '}
                    {!package_.enabled && <Badge tone="neutral">disabled</Badge>}
                  </p>
                  {package_.description && (
                    <p className="text-xs text-slate-500">{package_.description}</p>
                  )}
                </div>
                <Button size="sm" variant="secondary" onClick={() => { setCertDialogFor(package_.id); setFormError(null) }}>
                  <Plus className="h-3.5 w-3.5" /> Register certificate
                </Button>
              </div>

              {package_.certificates.length > 0 ? (
                <Table className="border-0">
                  <thead>
                    <tr>
                      <Th>Friendly name</Th>
                      <Th>Type</Th>
                      <Th>Store</Th>
                      <Th>SHA-256 fingerprint</Th>
                      <Th>Expires</Th>
                      <Th>Enabled</Th>
                      <Th />
                    </tr>
                  </thead>
                  <tbody>
                    {package_.certificates.map((cert) => (
                      <Tr key={cert.id}>
                        <Td className="font-medium text-slate-800">{cert.friendly_name}</Td>
                        <Td>{cert.certificate_type === 'ROOT' ? 'Root CA' : 'Intermediate CA'}</Td>
                        <Td>
                          <Badge tone="info">LocalMachine\{cert.destination_store}</Badge>
                        </Td>
                        <Td className="font-mono text-[11px]">{cert.fingerprint_sha256.slice(0, 32)}…</Td>
                        <Td>{cert.not_after ? new Date(cert.not_after).toLocaleDateString() : '—'}</Td>
                        <Td>
                          <Checkbox
                            label=""
                            checked={cert.enabled}
                            aria-label={`Toggle ${cert.friendly_name}`}
                            onChange={(event) =>
                              toggleCertificate.mutate({ id: cert.id, enabled: event.target.checked })
                            }
                          />
                        </Td>
                        <Td className="text-right">
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Delete ${cert.friendly_name}`}
                            onClick={() => {
                              if (window.confirm(`Delete certificate '${cert.friendly_name}'?`))
                                removeCertificate.mutate(cert.id)
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5 text-red-500" />
                          </Button>
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <p className="px-4 py-3 text-xs text-slate-400">No certificates registered in this package.</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Package dialog */}
      <Dialog
        open={packageDialogOpen}
        onClose={() => setPackageDialogOpen(false)}
        title="Add certificate package"
        footer={
          <>
            <Button variant="secondary" onClick={() => setPackageDialogOpen(false)}>Cancel</Button>
            <Button loading={createPackage.isPending} onClick={() => createPackage.mutate()}>Create</Button>
          </>
        }
      >
        {formError && <div className="mb-3"><Alert tone="danger">{formError}</Alert></div>}
        <FormRow label="Name" htmlFor="pkg-name" required>
          <Input id="pkg-name" value={packageName} onChange={(event) => setPackageName(event.target.value)} />
        </FormRow>
        <FormRow label="Description" htmlFor="pkg-desc">
          <Input id="pkg-desc" value={packageDescription}
                 onChange={(event) => setPackageDescription(event.target.value)} />
        </FormRow>
      </Dialog>

      {/* Certificate dialog */}
      <Dialog
        open={certDialogFor !== null}
        onClose={() => setCertDialogFor(null)}
        title="Register certificate"
        wide
        footer={
          <>
            <Button variant="secondary" onClick={() => setCertDialogFor(null)}>Cancel</Button>
            <Button loading={registerCertificate.isPending} onClick={() => registerCertificate.mutate()}>
              Register
            </Button>
          </>
        }
      >
        {formError && <div className="mb-3"><Alert tone="danger">{formError}</Alert></div>}
        <FormRow label="Friendly name" htmlFor="cert-name" required>
          <Input id="cert-name" value={certForm.friendly_name}
                 onChange={(event) => setCertForm({ ...certForm, friendly_name: event.target.value })} />
        </FormRow>
        <FormRow label="Certificate type" htmlFor="cert-type" required
                 hint="Determines the destination store on the target VM.">
          <Select id="cert-type" value={certForm.certificate_type}
                  onChange={(event) =>
                    setCertForm({ ...certForm, certificate_type: event.target.value as 'ROOT' | 'INTERMEDIATE' })
                  }>
            <option value="ROOT">Trusted Root Certification Authorities (LocalMachine\Root)</option>
            <option value="INTERMEDIATE">Intermediate Certification Authorities (LocalMachine\CA)</option>
          </Select>
        </FormRow>
        <FormRow label="PEM body" htmlFor="cert-pem" required
                 hint={'Paste the public certificate beginning with -----BEGIN CERTIFICATE-----'}>
          <Textarea id="cert-pem" rows={8} className="font-mono text-xs" value={certForm.pem_body}
                    onChange={(event) => setCertForm({ ...certForm, pem_body: event.target.value })} />
        </FormRow>
      </Dialog>
    </div>
  )
}
