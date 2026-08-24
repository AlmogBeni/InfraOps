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

import { api, setAccessToken } from '@/lib/api'
import type { UserOut } from '@/types/api'

interface AuthContextValue {
  user: UserOut | null
  initializing: boolean
  login: (username: string, password: string) => Promise<UserOut>
  logout: () => Promise<void>
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

  useEffect(() => {
    let cancelled = false
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
    window.addEventListener('infraops:user', onRefreshed)
    return () => {
      cancelled = true
      window.removeEventListener('infraops:user', onRefreshed)
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

  const logout = useCallback(async () => {
    await api.logout()
    applyUser(null)
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
    () => ({ user, initializing, login, logout, hasPermission, hasRole }),
    [user, initializing, login, logout, hasPermission, hasRole],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
