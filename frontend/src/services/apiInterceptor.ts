/**
 * API Interceptor — Wraps fetch to handle authentication errors
 *
 * If the backend returns 401 (Unauthorized) or 403 (Forbidden),
 * this interceptor will trigger session expiry and redirect to login.
 */

import { triggerSessionExpired, getToken } from '../hooks/useCurrentUser'

export interface FetchOptions extends RequestInit {
  skipInterceptor?: boolean
}

// Capture the native fetch at module load time — BEFORE enableGlobalFetchInterception
// replaces window.fetch. apiFetch must always call _nativeFetch directly to avoid
// the infinite loop: window.fetch → apiFetch → window.fetch → apiFetch → …
const _nativeFetch = globalThis.fetch.bind(globalThis)

/**
 * Enhanced fetch wrapper that handles auth errors.
 * Adds Authorization header automatically and handles 401/403 responses.
 * Always uses the native browser fetch internally — never the intercepted version.
 */
export async function apiFetch(url: string, options: FetchOptions = {}) {
  const { skipInterceptor = false, ...fetchOptions } = options

  // Add authorization header if not already present
  const headers = new Headers(fetchOptions.headers || {})
  if (!headers.has('Authorization')) {
    const token = getToken()
    if (token) {
      headers.set('Authorization', `Bearer ${token}`)
    }
  }

  // Use _nativeFetch — NOT window.fetch — to avoid infinite recursion
  const response = await _nativeFetch(url, {
    ...fetchOptions,
    headers,
  })

  // Handle authentication errors (unless explicitly skipped)
  if (!skipInterceptor) {
    if (response.status === 401) {
      console.warn('[API] Received 401 Unauthorized — session expired')
      triggerSessionExpired()
      throw new Error('Session expired. Please log in again.')
    }

    if (response.status === 403) {
      console.warn('[API] Received 403 Forbidden — access revoked')
      triggerSessionExpired()
      throw new Error('Access denied. Please log in again.')
    }
  }

  return response
}

/**
 * Replace window.fetch with our interceptor so ALL fetch calls get auth
 * headers and 401/403 handling automatically.
 *
 * Safe to call multiple times — each call re-wraps the current window.fetch
 * but apiFetch always escapes via _nativeFetch captured at module load time.
 */
export function enableGlobalFetchInterception() {
  const originalFetch = window.fetch
  window.fetch = function(...args: any[]) {
    const [url, options] = args as [string, FetchOptions]

    // Pass auth endpoints straight through to avoid intercepting login/logout
    if (url?.includes('/auth/login') || url?.includes('/auth/logout')) {
      return originalFetch.call(window, url, options)
    }

    return apiFetch(url, options)
  }
}
