import { describe, expect, it } from 'vitest'

import {
  readPublicCertificateFile,
  validateCertificateFile,
} from '@/features/admin/certificate-file'

describe('certificate file validation', () => {
  it('accepts a supported non-empty public certificate file', async () => {
    const body = '-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----'
    const file = new File([body], 'corporate-root.pem', { type: 'application/x-pem-file' })
    expect(validateCertificateFile(file).valid).toBe(true)
    expect(await readPublicCertificateFile(file)).toBe(body)
  })

  it('rejects unsupported formats and empty files', () => {
    expect(validateCertificateFile(new File(['x'], 'certificate.pfx')).valid).toBe(false)
    expect(validateCertificateFile(new File([], 'empty.pem')).message).toMatch(/empty/i)
  })

  it('rejects private-key material', async () => {
    const file = new File([
      '-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----',
    ], 'server.key')
    await expect(readPublicCertificateFile(file)).rejects.toThrow(/Unsupported certificate format/i)

    const disguised = new File([
      '-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----',
    ], 'server.pem')
    await expect(readPublicCertificateFile(disguised)).rejects.toThrow(/Private-key/i)
  })
})
