/** Authentication context: session state, login/logout and permission checks. */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import {
  DEFAULT_SESSION_POLICY,
  rememberSignOutReason,
  type SignOutReason,
} from '@/features/auth/session'
import { api, setAccessToken } from '@/lib/api'
import type { SessionPolicy, UserOut } from '@/types/api'

interface AuthContextValue {
  user: UserOut | null
  initializing: boolean
  policy: SessionPolicy
  login: (username: string, password: string) => Promise<UserOut>
  logout: (reason?: SignOutReason) => Promise<void>
  hasPermission: (permission: string) => boolean
  hasRole: (...roles: string[]) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

const USER_STORAGE_KEY = 'infraops.user'

function readStoredUser(): UserOut | null {
  try {
    const raw = localStorage.getItem(USER_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as UserOut) : null
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(readStoredUser())
  const [initializing, setInitializing] = useState(true)
  const [policy, setPolicy] = useState<SessionPolicy>(DEFAULT_SESSION_POLICY)

  useEffect(() => {
    let cancelled = false
    api
      .sessionPolicy()
      .then((next) => {
        if (!cancelled) setPolicy(next)
      })
      .catch(() => {
        /* keep the conservative defaults */
      })
    async function bootstrap() {
      try {
        const current = await api.me()
        if (!cancelled) applyUser(current)
      } catch {
        if (!cancelled) applyUser(null)
      } finally {
        if (!cancelled) setInitializing(false)
      }
    }
    void bootstrap()

    function onRefreshed(event: Event) {
      const detail = (event as CustomEvent<UserOut>).detail
      if (detail) applyUser(detail)
    }
    // Signing out in one tab signs out every tab of this browser.
    function onStorage(event: StorageEvent) {
      if (event.key === USER_STORAGE_KEY && event.newValue === null) applyUser(null)
    }
    window.addEventListener('infraops:user', onRefreshed)
    window.addEventListener('storage', onStorage)
    return () => {
      cancelled = true
      window.removeEventListener('infraops:user', onRefreshed)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  function applyUser(next: UserOut | null) {
    setUser(next)
    if (next) localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(next))
    else {
      localStorage.removeItem(USER_STORAGE_KEY)
      setAccessToken(null)
    }
  }

  const login = useCallback(async (username: string, password: string) => {
    const token = await api.login(username, password)
    applyUser(token.user)
    return token.user
  }, [])

  const logout = useCallback(async (reason?: SignOutReason) => {
    if (reason) rememberSignOutReason(reason)
    try {
      await api.logout()
    } finally {
      applyUser(null)
    }
  }, [])

  const hasPermission = useCallback(
    (permission: string) => Boolean(user?.permissions.includes(permission)),
    [user],
  )

  const hasRole = useCallback(
    (...roles: string[]) => Boolean(user && roles.some((role) => user.roles.includes(role))),
    [user],
  )

  const value = useMemo(
    () => ({ user, initializing, policy, login, logout, hasPermission, hasRole }),
    [user, initializing, policy, login, logout, hasPermission, hasRole],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
