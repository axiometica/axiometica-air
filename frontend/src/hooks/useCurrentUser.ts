import { useState, useEffect, useCallback } from 'react'

export interface CurrentUser {
  id: string
  name: string
  email: string | null
  role: 'admin' | 'itom_admin' | 'operator' | 'viewer' | 'automation'
}

const TOKEN_KEY = 'ap_token'

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

// Global logout callback for API interceptor
let globalLogoutCallback: (() => void) | null = null
export const setGlobalLogoutCallback = (callback: () => void) => {
  globalLogoutCallback = callback
}
export const triggerSessionExpired = () => {
  clearToken()
  if (globalLogoutCallback) {
    globalLogoutCallback()
  }
}

export function useCurrentUser() {
  const [user, setUser]       = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchMe = useCallback(async () => {
    const token = getToken()
    if (!token) { setLoading(false); return }
    try {
      const res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        setUser(await res.json())
      } else {
        clearToken()
        setUser(null)
      }
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchMe() }, [fetchMe])

  // Periodic token validation every 30 minutes
  useEffect(() => {
    const interval = setInterval(() => {
      fetchMe()
    }, 30 * 60 * 1000) // 30 minutes
    return () => clearInterval(interval)
  }, [fetchMe])

  const logout = useCallback(async () => {
    const token = getToken()
    if (token) {
      await fetch('/api/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${token}` } }).catch(() => {})
    }
    clearToken()
    setUser(null)
  }, [])

  // Register logout callback for API interceptor
  useEffect(() => {
    setGlobalLogoutCallback(logout)
  }, [logout])

  const isAdmin      = user?.role === 'admin'
  const isITOMAdmin  = user?.role === 'admin' || user?.role === 'itom_admin'
  const isOperator   = user?.role === 'admin' || user?.role === 'itom_admin' || user?.role === 'operator'
  const isViewer     = !!user

  return { user, loading, logout, refetch: fetchMe, isAdmin, isITOMAdmin, isOperator, isViewer }
}
