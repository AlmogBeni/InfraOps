import { Boxes, KeyRound, LockKeyhole, ShieldCheck } from 'lucide-react'
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
    <main className="relative min-h-screen overflow-hidden bg-[#151a19] px-5 py-8 text-white sm:px-8">
      <div className="absolute inset-0 opacity-30 [background-image:linear-gradient(rgba(216,240,106,0.06)_1px,transparent_1px),linear-gradient(90deg,rgba(216,240,106,0.06)_1px,transparent_1px)] [background-size:48px_48px]" />
      <div className="absolute -left-40 top-32 h-96 w-96 rounded-full bg-brand-500/20 blur-[100px]" />
      <div className="absolute -right-40 bottom-0 h-96 w-96 rounded-full bg-accent-500/15 blur-[110px]" />

      <div className="relative mx-auto flex min-h-[calc(100vh-4rem)] max-w-6xl flex-col">
        <header className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#d8f06a] text-[#18201c]"><Boxes className="h-5 w-5" /></span>
            <div><p className="text-sm font-bold tracking-[-0.02em]">InfraOps</p><p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-white/40">Virtual estate</p></div>
          </div>
          <span className="hidden items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/45 sm:flex"><span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Secure control plane</span>
        </header>

        <div className="my-auto grid items-center gap-12 py-16 lg:grid-cols-[minmax(0,1fr)_420px]">
          <section className="max-w-2xl">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#d8f06a]">Infrastructure operations workspace</p>
            <h1 className="mt-5 text-4xl font-semibold leading-[1.08] tracking-[-0.045em] sm:text-5xl lg:text-6xl">
              Your virtual estate,<br /><span className="text-white/45">clear and controlled.</span>
            </h1>
            <p className="mt-6 max-w-xl text-sm leading-6 text-white/55">
              Create datacenter-aware virtual machines, follow deployments in real time, and keep every operational change understandable and auditable.
            </p>
            <div className="mt-10 grid gap-3 sm:grid-cols-3">
              {[
                [ShieldCheck, 'Validated', 'Live inventory and policy checks'],
                [LockKeyhole, 'Audited', 'Immutable operator activity'],
                [KeyRound, 'Role scoped', 'Least-privilege access'],
              ].map(([Icon, title, text]) => {
                const ItemIcon = Icon as typeof ShieldCheck
                return (
                  <div key={String(title)} className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                    <ItemIcon className="h-4 w-4 text-[#d8f06a]" />
                    <p className="mt-3 text-xs font-semibold">{String(title)}</p>
                    <p className="mt-1 text-[11px] leading-4 text-white/40">{String(text)}</p>
                  </div>
                )
              })}
            </div>
          </section>

          <section className="rounded-3xl border border-white/15 bg-white p-7 text-[#17201c] shadow-[0_32px_100px_rgba(0,0,0,0.35)] sm:p-8">
            <div className="mb-7">
              <p className="console-kicker">Operator access</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-[-0.035em]">Sign in to InfraOps</h2>
              <p className="mt-2 text-xs leading-5 text-[#68736d]">Use the account assigned by your infrastructure administrator.</p>
            </div>
            <form onSubmit={handleSubmit}>
              {error && <div className="mb-5"><Alert tone="danger" title="Authentication failed">{error}</Alert></div>}
              <FormRow label="Username" htmlFor="login-username" required>
                <Input id="login-username" autoComplete="username" autoFocus value={username} onChange={(event) => setUsername(event.target.value)} required />
              </FormRow>
              <FormRow label="Password" htmlFor="login-password" required>
                <Input id="login-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
              </FormRow>
              <Button type="submit" className="mt-2 w-full" loading={submitting}>Sign in securely</Button>
              <p className="mt-5 border-t border-[#e2e6e1] pt-4 text-center text-[10px] uppercase tracking-[0.12em] text-[#87908a]">Authorized infrastructure personnel only</p>
            </form>
          </section>
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 text-[10px] uppercase tracking-[0.12em] text-white/30"><span>InfraOps virtualization control plane</span><span>Protected session · audited activity</span></footer>
      </div>
    </main>
  )
}
