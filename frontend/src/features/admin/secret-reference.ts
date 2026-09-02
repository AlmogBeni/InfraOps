const SAFE_REFERENCE_CHARACTERS =
  /^[a-z0-9][a-z0-9_.-]*(?:\/[a-z0-9][a-z0-9_.-]*)*$/

/** Match the backend/provider contract for a safe, slash-separated secret path. */
export function isValidSecretReference(value: string): boolean {
  if (value.length < 2 || value.length > 150) return false
  if (!SAFE_REFERENCE_CHARACTERS.test(value)) return false
  return value.split('/').every((segment) => segment !== '.' && segment !== '..')
}

export const SECRET_REFERENCE_HINT =
  'Use 2-150 lowercase letters, digits, dots, dashes, underscores, or safe path separators (/).'
