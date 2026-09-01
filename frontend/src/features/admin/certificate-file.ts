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
    return { valid: false, message: 'Unsupported certificate format. Use a public X.509 .crt, .cer or .pem file.' }
  }
  return { valid: true, message: 'File type and size are supported.' }
}

function readBytes(file: File): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      if (!(reader.result instanceof ArrayBuffer)) {
        reject(new Error('The certificate file could not be read.'))
        return
      }
      resolve(new Uint8Array(reader.result))
    }
    reader.onerror = () => reject(new Error('The certificate file could not be read.'))
    reader.readAsArrayBuffer(file)
  })
}

function hasCompleteDerSequence(bytes: Uint8Array): boolean {
  if (bytes.length < 4 || bytes[0] !== 0x30) return false

  const firstLengthByte = bytes[1]
  if (firstLengthByte < 0x80) return firstLengthByte + 2 === bytes.length

  const lengthByteCount = firstLengthByte & 0x7f
  if (lengthByteCount === 0 || lengthByteCount > 4 || bytes.length < 2 + lengthByteCount) return false

  let payloadLength = 0
  for (let index = 0; index < lengthByteCount; index += 1) {
    payloadLength = (payloadLength * 256) + bytes[2 + index]
  }
  return 2 + lengthByteCount + payloadLength === bytes.length
}

function derToPem(bytes: Uint8Array): string {
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000))
  }
  const base64 = btoa(binary)
  const lines = base64.match(/.{1,64}/g)?.join('\n') ?? base64
  return `-----BEGIN CERTIFICATE-----\n${lines}\n-----END CERTIFICATE-----`
}

export async function readPublicCertificateFile(file: File): Promise<string> {
  const validation = validateCertificateFile(file)
  if (!validation.valid) throw new Error(validation.message)

  const bytes = await readBytes(file)
  const body = new TextDecoder('utf-8').decode(bytes).replace(/^\uFEFF/, '').trim()
  if (/-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----/.test(body)) {
    throw new Error('Private-key files are not supported. Upload only a public X.509 certificate.')
  }

  if (body.includes('-----BEGIN CERTIFICATE-----') && body.includes('-----END CERTIFICATE-----')) {
    return body
  }

  if (extensionOf(file.name) !== '.pem' && hasCompleteDerSequence(bytes)) {
    return derToPem(bytes)
  }

  throw new Error('The file is not a recognized PEM or DER encoded X.509 certificate.')
}
