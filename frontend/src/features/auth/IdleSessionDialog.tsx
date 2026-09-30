/** Warns an inactive user and signs them out when they do not respond. */

import { useQueryClient } from '@tanstack/react-query'
import { Clock3, LogOut } from 'lucide-react'
import { useCallback } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { useIdleTimeout } from '@/features/auth/useIdleTimeout'
import { useAuth } from '@/lib/auth'

function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return minutes > 0 ? `${minutes}:${String(rest).padStart(2, '0')}` : `${rest} s`
}

export function IdleSessionDialog() {
  const { user, policy, logout } = useAuth()
  const queryClient = useQueryClient()

  // Signing out clears the user, and the route guard then shows the login
  // page. Cached data is dropped so the next user never sees it.
  const signOut = useCallback(
    async (reason?: 'idle') => {
      await logout(reason).catch(() => undefined)
      queryClient.clear()
    },
    [logout, queryClient],
  )

  const { warning, secondsLeft, stayActive } = useIdleTimeout({
    enabled: Boolean(user),
    timeoutSeconds: policy.idle_timeout_seconds,
    warningSeconds: policy.idle_warning_seconds,
    onExpire: () => void signOut('idle'),
  })

  const idleMinutes = Math.round(policy.idle_timeout_seconds / 60)

  return (
    <Dialog
      open={warning}
      onClose={stayActive}
      title="Are you still there?"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={() => void signOut()}>
            <LogOut className="h-3.5 w-3.5" aria-hidden /> Sign out
          </Button>
          <Button size="sm" onClick={stayActive} autoFocus>
            Stay signed in
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-amber-50 text-amber-700">
          <Clock3 className="h-5 w-5" aria-hidden />
        </span>
        <div className="text-sm text-[#3d4741]">
          <p>
            You have been inactive for {idleMinutes} {idleMinutes === 1 ? 'minute' : 'minutes'}. For security,
            you will be signed out automatically.
          </p>
          <p className="mt-3 text-2xl font-semibold tabular-nums text-[#1b2420]" aria-live="polite">
            {formatCountdown(secondsLeft)}
          </p>
        </div>
      </div>
    </Dialog>
  )
}
