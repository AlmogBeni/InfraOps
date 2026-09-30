/** Explains why sessions cannot survive a reload when served over plain HTTP. */

import { Alert } from '@/components/ui/feedback'
import { refreshCookieWillBeDropped } from '@/features/auth/session'
import { useAuth } from '@/lib/auth'

export function InsecureTransportNotice() {
  const { policy } = useAuth()
  if (!refreshCookieWillBeDropped(policy, window.location)) return null
  return (
    <Alert tone="warning" title="InfraOps is being served over unencrypted HTTP">
      Your browser discards the secure session cookie on HTTP connections, so reloading the page signs you
      out. Open InfraOps through HTTPS (for example{' '}
      <span className="font-mono">https://{window.location.hostname}</span>) — an administrator can enable
      HTTPS as described in docs/production-deployment.md.
    </Alert>
  )
}
