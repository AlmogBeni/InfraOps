import { afterEach, describe, expect, it, vi } from 'vitest'

import { API_BASE, api, setAccessToken } from '@/lib/api'
import type { UserOut } from '@/types/api'

const USER: UserOut = {
  id: 'user-1',
  username: 'operator',
  email: 'operator@example.test',
  full_name: 'Test Operator',
  roles: ['operator'],
  permissions: ['infrastructure.read'],
}

function response(status: number, payload: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response
}

describe('API session recovery', () => {
  afterEach(() => {
    setAccessToken(null)
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('uses the refresh cookie to restore a session after the in-memory token is lost', async () => {
    setAccessToken(null)
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(401, { error: { code: 'authentication_error', message: 'Missing bearer token.' } }))
      .mockResolvedValueOnce(response(200, { access_token: 'refreshed-access-token', expires_in: 900, user: USER }))
      .mockResolvedValueOnce(response(200, USER))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.me()).resolves.toEqual(USER)

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock).toHaveBeenNthCalledWith(1, `${API_BASE}/auth/me`, expect.objectContaining({
      credentials: 'include',
      headers: {},
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, `${API_BASE}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    })
    expect(fetchMock).toHaveBeenNthCalledWith(3, `${API_BASE}/auth/me`, expect.objectContaining({
      credentials: 'include',
      headers: { Authorization: 'Bearer refreshed-access-token' },
    }))
  })
})
