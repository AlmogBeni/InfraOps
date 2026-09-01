export const MAX_CERTIFICATE_FILE_BYTES = 100_000
export const SUPPORTED_CERTIFICATE_EXTENSIONS = ['.pem', '.crt', '.cer'] as const

export interface CertificateFileValidation {
  valid: boolean
  message: string
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

export function validateCertificateFile(file: File): CertificateFileValidation {
  if (file.size === 0) return { valid: false, message: 'Certificate file is empty.' }
  if (file.size > MAX_CERTIFICATE_FILE_BYTES) {
    return { valid: false, message: 'Certificate file exceeds the 100 KB limit.' }
  }
  const extension = extensionOf(file.name)
  if (!SUPPORTED_CERTIFICATE_EXTENSIONS.includes(extension as (typeof SUPPORTED_CERTIFICATE_EXTENSIONS)[number])) {
    return { valid: false, message: 'Unsupported certificate format. Use a PEM-encoded .pem, .crt or .cer file.' }
  }
  return { valid: true, message: 'File type and size are supported.' }
}

export async function readPublicCertificateFile(file: File): Promise<string> {
  const validation = validateCertificateFile(file)
  if (!validation.valid) throw new Error(validation.message)
  const body = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(new Error('The certificate file could not be read.'))
    reader.readAsText(file, 'utf-8')
  })
  if (/-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----/.test(body)) {
    throw new Error('Private-key files are not supported. Upload only a public X.509 certificate.')
  }
  if (!body.includes('-----BEGIN CERTIFICATE-----') || !body.includes('-----END CERTIFICATE-----')) {
    throw new Error('The file is not a PEM-encoded X.509 certificate.')
  }
  return body.trim()
}
