import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, Upload, X } from 'lucide-react'
import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Alert, Badge, EmptyState, Spinner } from '@/components/ui/feedback'
import { Checkbox, FormRow, Input, Select, Textarea } from '@/components/ui/form-controls'
import { PageHeader } from '@/components/ui/page'
import { Table, Td, Th, Tr } from '@/components/ui/table'
import { api } from '@/lib/api'
import { readPublicCertificateFile, validateCertificateFile } from '@/features/admin/certificate-file'

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
  const [certFile, setCertFile] = useState<File | null>(null)
  const [fileValidation, setFileValidation] = useState<{ valid: boolean; message: string } | null>(null)
  const [fileReading, setFileReading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const fileReadGenerationRef = useRef(0)

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
      fileReadGenerationRef.current += 1
      setCertDialogFor(null)
      setCertForm({ friendly_name: '', certificate_type: 'ROOT', pem_body: '' })
      setCertFile(null)
      setFileValidation(null)
      setFileReading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
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

  function closeCertificateDialog() {
    fileReadGenerationRef.current += 1
    setCertDialogFor(null)
    setCertForm({ friendly_name: '', certificate_type: 'ROOT', pem_body: '' })
    setCertFile(null)
    setFileValidation(null)
    setFileReading(false)
    setFormError(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  async function selectCertificateFile(file: File | undefined) {
    if (!file) return
    const generation = ++fileReadGenerationRef.current
    setCertFile(file)
    setFileReading(true)
    setFileValidation(null)
    setCertForm((current) => ({ ...current, pem_body: '' }))
    const validation = validateCertificateFile(file)
    if (!validation.valid) {
      setFileValidation(validation)
      setFileReading(false)
      return
    }
    try {
      const pemBody = await readPublicCertificateFile(file)
      if (generation !== fileReadGenerationRef.current) return
      setCertForm((current) => ({
        ...current,
        friendly_name: current.friendly_name || file.name.replace(/\.[^.]+$/, ''),
        pem_body: pemBody,
      }))
      setFileValidation({ valid: true, message: 'Certificate loaded; ready for server validation.' })
    } catch (error) {
      if (generation !== fileReadGenerationRef.current) return
      setCertForm((current) => ({ ...current, pem_body: '' }))
      setFileValidation({
        valid: false,
        message: error instanceof Error ? error.message : 'The certificate file could not be read.',
      })
    } finally {
      if (generation === fileReadGenerationRef.current) setFileReading(false)
    }
  }

  function openCertificateFilePicker() {
    if (!fileInputRef.current) return
    fileInputRef.current.value = ''
    fileInputRef.current.click()
  }

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Trust administration"
        title="Certificate deployment packages"
        description="Publish verified public X.509 trust chains for operator selection. Fingerprints, validity, and Windows stores are validated server-side."
        actions={<Button size="sm" onClick={() => setPackageDialogOpen(true)}><Plus className="h-4 w-4" /> Add package</Button>}
        meta={<span>{packages.data?.length ?? 0} package(s) configured</span>}
      />

      {packages.isLoading ? (
        <div className="flex h-40 items-center justify-center">
          <Spinner />
        </div>
      ) : packages.isError ? (
        <Alert tone="danger" title="Certificate catalog unavailable">
          Certificate packages could not be loaded.{' '}
          <Button size="sm" variant="secondary" onClick={() => void packages.refetch()}>Retry</Button>
        </Alert>
      ) : (packages.data ?? []).length === 0 ? (
        <EmptyState
          title="No certificate packages"
          description="Create a package, then register public root or intermediate CA certificates."
          action={<Button size="sm" onClick={() => setPackageDialogOpen(true)}><Plus className="h-3.5 w-3.5" /> Add package</Button>}
        />
      ) : (
        <div className="space-y-4">
          {(packages.data ?? []).map((package_) => (
            <div key={package_.id} className="overflow-hidden rounded-md border border-slate-300 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-[#f5f7f9] px-4 py-2.5">
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
        onClose={closeCertificateDialog}
        title="Register certificate"
        wide
        footer={
          <>
            <Button variant="secondary" onClick={closeCertificateDialog}>Cancel</Button>
            <Button
              loading={registerCertificate.isPending}
              disabled={fileReading || !certForm.friendly_name || !certForm.pem_body || fileValidation?.valid === false}
              onClick={() => registerCertificate.mutate()}
            >
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
        <FormRow
          label="Certificate file"
          htmlFor="cert-file"
          hint="Public X.509 only; .crt and .cer may use PEM or DER encoding. .pem is also supported. Maximum 100 KB."
          error={fileValidation?.valid === false ? fileValidation.message : undefined}
        >
          <input
            ref={fileInputRef}
            id="cert-file"
            type="file"
            className="sr-only"
            accept=".crt,.cer,.pem,application/pkix-cert,application/x-x509-ca-cert,application/x-pem-file"
            onChange={(event) => void selectCertificateFile(event.target.files?.[0])}
          />
          {!certFile ? (
            <Button type="button" variant="secondary" onClick={openCertificateFilePicker}>
              <Upload className="h-4 w-4" /> Choose file
            </Button>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-800">{certFile.name}</p>
                <p className="text-xs text-slate-500">
                  {certFile.type || 'Certificate file'} · {(certFile.size / 1024).toFixed(1)} KB
                </p>
                {fileReading && <p className="text-xs text-slate-500">Reading and validating…</p>}
                {fileValidation?.valid && <p className="text-xs font-medium text-emerald-700">{fileValidation.message}</p>}
              </div>
              <div className="flex gap-1">
                <Button type="button" size="sm" variant="secondary" onClick={openCertificateFilePicker}>
                  Replace
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label="Remove selected certificate file"
                  onClick={() => {
                    fileReadGenerationRef.current += 1
                    setCertFile(null)
                    setFileValidation(null)
                    setFileReading(false)
                    setCertForm((current) => ({ ...current, pem_body: '' }))
                    if (fileInputRef.current) fileInputRef.current.value = ''
                  }}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </FormRow>
        <div className="my-3 flex items-center gap-3 text-xs uppercase tracking-wide text-slate-400">
          <span className="h-px flex-1 bg-slate-200" /> or paste PEM <span className="h-px flex-1 bg-slate-200" />
        </div>
        <FormRow label="PEM body" htmlFor="cert-pem" required
                 hint={'Paste the public certificate beginning with -----BEGIN CERTIFICATE-----'}>
          <Textarea id="cert-pem" rows={8} className="font-mono text-xs" value={certForm.pem_body}
                    onChange={(event) => {
                      fileReadGenerationRef.current += 1
                      setCertFile(null)
                      setFileValidation(null)
                      setFileReading(false)
                      setCertForm({ ...certForm, pem_body: event.target.value })
                    }} />
        </FormRow>
      </Dialog>
    </div>
  )
}
