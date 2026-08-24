import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Alert } from '@/components/ui/feedback'
import { FormRow, Input } from '@/components/ui/form-controls'
import { useAuth } from '@/lib/auth'
import { ApiError } from '@/lib/api'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(username, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Sign-in failed. Please try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <span className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-lg bg-brand-600 text-lg font-bold text-white">
            IO
          </span>
          <h1 className="text-xl font-semibold text-white">InfraOps</h1>
          <p className="mt-1 text-xs text-slate-400">
            Internal Infrastructure Automation Platform
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-lg border border-slate-200 bg-white p-5 shadow-lg"
        >
          {error && (
            <div className="mb-4">
              <Alert tone="danger" title="Sign-in failed">
                {error}
              </Alert>
            </div>
          )}

          <FormRow label="Username" htmlFor="login-username" required>
            <Input
              id="login-username"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </FormRow>

          <FormRow label="Password" htmlFor="login-password" required>
            <Input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </FormRow>

          <Button type="submit" className="w-full" loading={submitting}>
            Sign in
          </Button>

          <p className="mt-4 text-center text-[11px] leading-4 text-slate-400">
            Development seed accounts: admin / operator / viewer — password from
            DEV_ADMIN_PASSWORD in your .env file.
          </p>
        </form>
      </div>
    </div>
  )
}
