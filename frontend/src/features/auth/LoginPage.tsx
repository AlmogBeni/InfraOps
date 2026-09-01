import { LockKeyhole, ServerCog, ShieldCheck, Workflow } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Alert } from '@/components/ui/feedback'
import { FormRow, Input } from '@/components/ui/form-controls'
import { ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'

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
      setError(err instanceof ApiError ? err.message : 'Sign-in failed. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="grid min-h-screen bg-[#07111f] lg:grid-cols-[minmax(420px,1.15fr)_minmax(420px,0.85fr)]">
      <section className="hidden min-h-screen flex-col justify-between border-r border-slate-800 p-12 lg:flex xl:p-16">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-md border border-brand-500/50 bg-brand-600/15 text-brand-100">
            <ServerCog className="h-5 w-5" aria-hidden />
          </span>
          <div>
            <p className="text-sm font-bold tracking-[0.12em] text-white">INFRAOPS</p>
            <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-slate-500">Virtualization control plane</p>
          </div>
        </div>

        <div className="max-w-2xl">
          <p className="console-kicker text-brand-500">Infrastructure operations</p>
          <h1 className="mt-3 text-4xl font-semibold leading-tight tracking-tight text-white xl:text-5xl">
            Provision with control.<br />Operate with evidence.
          </h1>
          <p className="mt-5 max-w-xl text-sm leading-6 text-slate-400">
            A focused administration surface for vSphere inventory, repeatable VM deployment,
            guest configuration, and audited operational execution.
          </p>

          <div className="mt-10 grid max-w-xl gap-px overflow-hidden rounded-md border border-slate-800 bg-slate-800 sm:grid-cols-3">
            {[
              [Workflow, 'Controlled workflows', 'Validated placement and ordered execution'],
              [ShieldCheck, 'Policy aligned', 'RBAC, preflight checks, and audit evidence'],
              [LockKeyhole, 'Secret safe', 'References only; credentials stay out of jobs'],
            ].map(([Icon, title, description]) => {
              const ItemIcon = Icon as typeof Workflow
              return (
                <div key={String(title)} className="bg-[#0b1727] p-4">
                  <ItemIcon className="h-4 w-4 text-brand-500" aria-hidden />
                  <p className="mt-3 text-xs font-semibold text-slate-100">{String(title)}</p>
                  <p className="mt-1 text-[11px] leading-4 text-slate-500">{String(description)}</p>
                </div>
              )
            })}
          </div>
        </div>

        <p className="text-[10px] uppercase tracking-[0.14em] text-slate-600">Authorized infrastructure personnel only</p>
      </section>

      <section className="flex min-h-screen items-center justify-center bg-[#edf1f5] px-5 py-10">
        <div className="w-full max-w-md">
          <div className="mb-6 flex items-center gap-3 lg:hidden">
            <span className="flex h-9 w-9 items-center justify-center rounded-md bg-brand-600 text-white">
              <ServerCog className="h-5 w-5" aria-hidden />
            </span>
            <p className="text-sm font-bold tracking-[0.1em] text-slate-950">INFRAOPS</p>
          </div>

          <div className="mb-6">
            <p className="console-kicker">Secure operator access</p>
            <h2 className="mt-1.5 text-2xl font-semibold tracking-tight text-slate-950">Sign in to the control plane</h2>
            <p className="mt-2 text-xs leading-5 text-slate-600">Use your assigned infrastructure operations account.</p>
          </div>

          <form onSubmit={handleSubmit} className="rounded-md border border-slate-300 bg-white p-6 shadow-[0_8px_30px_rgba(15,23,42,0.08)]">
            {error && (
              <div className="mb-4">
                <Alert tone="danger" title="Authentication failed">{error}</Alert>
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

            <Button type="submit" className="mt-1 w-full" loading={submitting}>Authenticate</Button>
            <div className="mt-5 flex items-start gap-2 border-t border-slate-200 pt-4 text-[11px] leading-4 text-slate-500">
              <LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
              Sessions are role-scoped and all infrastructure mutations are audited.
            </div>
          </form>
        </div>
      </section>
    </main>
  )
}
