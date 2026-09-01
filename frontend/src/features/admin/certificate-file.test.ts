import { describe, expect, it } from 'vitest'

import {
  readPublicCertificateFile,
  validateCertificateFile,
} from '@/features/admin/certificate-file'

describe('certificate file validation', () => {
  it('accepts a PEM-encoded CRT certificate file', async () => {
    const body = '-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----'
    const file = new File([body], 'corporate-root.crt', { type: 'application/x-pem-file' })
    expect(validateCertificateFile(file).valid).toBe(true)
    expect(await readPublicCertificateFile(file)).toBe(body)
  })

  it('normalizes a DER-encoded CRT file to PEM for server validation', async () => {
    const file = new File([new Uint8Array([0x30, 0x03, 0x02, 0x01, 0x01])], 'corporate-root.crt', {
      type: 'application/pkix-cert',
    })

    await expect(readPublicCertificateFile(file)).resolves.toBe(
      '-----BEGIN CERTIFICATE-----\nMAMCAQE=\n-----END CERTIFICATE-----',
    )
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

  it('rejects unrecognized CRT content before registration', async () => {
    const file = new File(['not a certificate'], 'broken.crt')
    await expect(readPublicCertificateFile(file)).rejects.toThrow(/not a recognized PEM or DER/i)
  })
})
