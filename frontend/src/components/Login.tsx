import { useState, useEffect, useRef, FormEvent } from 'react'
import { setToken } from '../hooks/useCurrentUser'
import axioLogo from '../assets/AxioLogo.svg'

interface LoginProps {
  onLogin: () => void
}

export default function Login({ onLogin }: LoginProps) {
  const [email, setEmail]         = useState('')
  const [password, setPassword]   = useState('')
  const [error, setError]         = useState<string | null>(null)
  const [startingUp, setStartingUp] = useState(false)
  const [loading, setLoading]     = useState(false)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Clear any pending retry on unmount
  useEffect(() => () => { if (retryTimer.current) clearTimeout(retryTimer.current) }, [])

  const doLogin = async (emailVal: string, passwordVal: string) => {
    setError(null)
    setStartingUp(false)
    setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: emailVal, password: passwordVal }),
      })

      if (res.ok) {
        const data = await res.json()
        setToken(data.access_token)
        setLoading(false)
        onLogin()
        return
      }

      // Actual authentication failure — credentials are wrong
      if (res.status === 401 || res.status === 403) {
        setError('Invalid email or password')
        setLoading(false)
        return
      }

      // Backend not yet ready (nginx is up but FastAPI hasn't started) — retry
      if (res.status === 502 || res.status === 503 || res.status === 504) {
        setStartingUp(true)
        retryTimer.current = setTimeout(() => doLogin(emailVal, passwordVal), 3000)
        return // keep loading=true — button stays in waiting state
      }

      setError(`Sign in failed (${res.status}). Please try again.`)
      setLoading(false)
    } catch {
      // Network error — backend not reachable yet, retry automatically
      setStartingUp(true)
      retryTimer.current = setTimeout(() => doLogin(emailVal, passwordVal), 3000)
      // keep loading=true
    }
  }

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (retryTimer.current) clearTimeout(retryTimer.current)
    doLogin(email, password)
  }

  const inputStyle: React.CSSProperties = {
    width: '100%',
    background: '#0d1117',
    border: '1px solid #2d3748',
    borderRadius: '8px',
    color: '#e2e8f0',
    fontSize: '0.9rem',
    padding: '0.6rem 0.75rem',
    outline: 'none',
    boxSizing: 'border-box',
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: '#0d1117',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif",
    }}>
      <div style={{
        background: '#1a1f2e',
        border: '1px solid #2d3748',
        borderRadius: '12px',
        padding: '2.5rem 2rem',
        width: '100%',
        maxWidth: '380px',
        boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
      }}>
        {/* Logo / branding */}
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <img
            src={axioLogo}
            alt="Axiometica AIR Platform"
            style={{
              height: '160px',
              width: 'auto',
              maxWidth: '100%',
              objectFit: 'contain',
              display: 'block',
              marginBottom: '1rem',
            }}
          />
          <p style={{ color: '#64748b', fontSize: '0.85rem', marginTop: '0.35rem' }}>
            Autonomous Incident Management
          </p>
        </div>

        <form onSubmit={handleSubmit}>
          {/* Email */}
          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', color: '#94a3b8', fontSize: '0.82rem', marginBottom: '0.4rem', fontWeight: 500 }}>
              Email address
            </label>
            <input
              type="email"
              name="email"
              autoComplete="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              autoFocus
              placeholder="Enter your email"
              style={inputStyle}
            />
          </div>

          {/* Password */}
          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{ display: 'block', color: '#94a3b8', fontSize: '0.82rem', marginBottom: '0.4rem', fontWeight: 500 }}>
              Password
            </label>
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              placeholder="Enter your password"
              style={inputStyle}
            />
          </div>

          {/* Auth error — only for genuine credential failures */}
          {error && (
            <div style={{
              background: 'rgba(220,38,38,0.12)',
              border: '1px solid rgba(220,38,38,0.3)',
              borderRadius: '8px',
              color: '#fca5a5',
              fontSize: '0.83rem',
              padding: '0.6rem 0.75rem',
              marginBottom: '1rem',
            }}>
              {error}
            </div>
          )}

          {/* Backend starting up — amber notice, auto-retrying */}
          {startingUp && !error && (
            <div style={{
              background: 'rgba(245,158,11,0.1)',
              border: '1px solid rgba(245,158,11,0.3)',
              borderRadius: '8px',
              color: '#fcd34d',
              fontSize: '0.83rem',
              padding: '0.6rem 0.75rem',
              marginBottom: '1rem',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
            }}>
              <span style={{ animation: 'login-spin 1.2s linear infinite', display: 'inline-block' }}>⟳</span>
              Backend is starting up — retrying automatically…
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              background: loading ? '#4338ca' : 'linear-gradient(135deg, #6366f1, #8b5cf6)',
              border: 'none',
              borderRadius: '8px',
              color: '#fff',
              fontSize: '0.92rem',
              fontWeight: 600,
              padding: '0.7rem',
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.7 : 1,
              transition: 'opacity 0.15s',
            }}
          >
            {startingUp ? 'Waiting for server…' : loading ? 'Signing in…' : 'Sign in'}
          </button>

          {/* Demo credentials — only rendered when this instance advertises
              itself as a public demo. Hidden on all normal installs. The
              button below one-clicks the demo login so a visitor doesn't
              have to copy/paste credentials.

              Both hostnames are checked: instance.axiometica.com is where
              the platform login lives, but visitors are more likely to land
              on demo.axiometica.com first (the marketing gateway) and be
              redirected here — so the button needs to show for anyone whose
              path started at either hostname. */}
          {(window.location.hostname === 'instance.axiometica.com'
             || window.location.hostname === 'demo.axiometica.com') && (
            <div style={{
              marginTop: 18, padding: 12, borderRadius: 8,
              background: '#0d1117', border: '1px solid #3d4557',
              fontSize: '0.8rem', color: '#a0aec0',
            }}>
              <div style={{ color: '#fbbf24', fontWeight: 600, marginBottom: 4 }}>
                Just exploring? Try the demo account
              </div>
              <div style={{ marginBottom: 8 }}>
                Read-only access to every module. Chat is available (20 messages/day).
              </div>
              <button
                type="button"
                onClick={() => {
                  setEmail('demo@axiometica.com')
                  setPassword('Demo@1234!')
                  // Let React flush the state then fire login.
                  setTimeout(() => doLogin('demo@axiometica.com', 'Demo@1234!'), 0)
                }}
                disabled={loading}
                style={{
                  width: '100%', padding: '0.5rem', borderRadius: 6,
                  border: '1px solid #78350f', background: 'transparent',
                  color: '#fbbf24', fontSize: '0.82rem', fontWeight: 500,
                  cursor: loading ? 'not-allowed' : 'pointer',
                }}
              >
                Sign in as demo@axiometica.com
              </button>
            </div>
          )}
        </form>
      </div>

      <style>{`
        @keyframes login-spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}
