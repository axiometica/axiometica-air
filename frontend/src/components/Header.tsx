import { useRef, useState, useEffect } from 'react'
import { MenuIcon } from './IconWrappers'
import { IconX, IconKey, IconLogout, IconChevronDown } from './icons'
import logo from '../assets/AxioLogo.svg'
import NotificationBell from './NotificationBell'
import HelpDocs from './HelpDocs'
import { CurrentUser, getToken } from '../hooks/useCurrentUser'

// Derive initials from a display name (e.g. "Mike Behar" → "MB")
function initials(name: string): string {
  return name
    .split(' ')
    .map(w => w[0] ?? '')
    .join('')
    .toUpperCase()
    .slice(0, 2)
}

const ROLE_CONFIG: Record<string, { label: string; bg: string; color: string }> = {
  admin:      { label: 'Admin',      bg: 'rgba(139,92,246,0.18)', color: '#a78bfa' },
  itom_admin: { label: 'ITOM Admin', bg: 'rgba(59,130,246,0.18)', color: '#60a5fa' },
  operator:   { label: 'Operator',   bg: 'rgba(16,185,129,0.18)', color: '#34d399' },
  viewer:     { label: 'Viewer',     bg: 'rgba(107,114,128,0.2)', color: '#9ca3af' },
  automation: { label: 'Automation', bg: 'rgba(245,158,11,0.18)', color: '#fbbf24' },
}

// ── Self-service Change Password modal ───────────────────────────────────────
interface ChangePasswordModalProps {
  darkMode: boolean
  onClose: () => void
}

function ChangePasswordModal({ darkMode, onClose }: ChangePasswordModalProps) {
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw]         = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [saving, setSaving]       = useState(false)
  const [error, setError]         = useState('')
  const [success, setSuccess]     = useState(false)

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '0.5rem 0.75rem',
    borderRadius: '8px',
    border: darkMode ? '1px solid #334155' : '1px solid #cbd5e1',
    background: darkMode ? '#1e293b' : '#f8fafc',
    color: darkMode ? '#e2e8f0' : '#0f172a',
    fontSize: '0.875rem',
    outline: 'none',
    boxSizing: 'border-box',
  }
  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: '0.75rem',
    fontWeight: 600,
    color: darkMode ? '#94a3b8' : '#64748b',
    marginBottom: '0.3rem',
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
  }

  async function handleSubmit() {
    setError('')
    if (newPw !== confirmPw) { setError('New passwords do not match.'); return }
    if (newPw.length < 8)    { setError('New password must be at least 8 characters.'); return }

    setSaving(true)
    try {
      const token = getToken()
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data.detail ?? 'Password change failed.')
        return
      }
      setSuccess(true)
      setTimeout(onClose, 1500)
    } catch {
      setError('Network error — please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 2000,
      background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{
        background: darkMode ? '#1e293b' : '#fff',
        border: darkMode ? '1px solid #334155' : '1px solid #e2e8f0',
        borderRadius: '16px',
        padding: '1.75rem',
        width: '380px',
        maxWidth: '95vw',
        boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.25rem' }}>
          <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: darkMode ? '#e2e8f0' : '#0f172a' }}>
            Change Password
          </h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer',
            color: darkMode ? '#64748b' : '#94a3b8', padding: '0.2rem' }}>
            <IconX size={18} />
          </button>
        </div>

        {success ? (
          <div style={{ textAlign: 'center', padding: '1rem 0', color: '#34d399', fontWeight: 600 }}>
            ✓ Password changed successfully
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div>
              <label style={labelStyle}>Current password</label>
              <input type="password" value={currentPw} onChange={e => setCurrentPw(e.target.value)}
                placeholder="Enter current password" style={inputStyle} autoFocus />
            </div>
            <div>
              <label style={labelStyle}>New password</label>
              <input type="password" value={newPw} onChange={e => setNewPw(e.target.value)}
                placeholder="Min. 8 characters" style={inputStyle} />
            </div>
            <div>
              <label style={labelStyle}>Confirm new password</label>
              <input type="password" value={confirmPw} onChange={e => setConfirmPw(e.target.value)}
                placeholder="Repeat new password" style={inputStyle}
                onKeyDown={e => e.key === 'Enter' && handleSubmit()} />
            </div>

            {error && (
              <div style={{ color: '#f87171', fontSize: '0.8rem', background: 'rgba(239,68,68,0.1)',
                padding: '0.5rem 0.75rem', borderRadius: '6px' }}>
                {error}
              </div>
            )}

            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '0.25rem' }}>
              <button onClick={onClose} style={{
                padding: '0.5rem 1rem', borderRadius: '8px', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem',
                background: 'transparent', border: darkMode ? '1px solid #334155' : '1px solid #cbd5e1',
                color: darkMode ? '#94a3b8' : '#64748b',
              }}>
                Cancel
              </button>
              <button onClick={handleSubmit} disabled={saving || !currentPw || !newPw || !confirmPw} style={{
                padding: '0.5rem 1.1rem', borderRadius: '8px', cursor: saving ? 'not-allowed' : 'pointer',
                fontWeight: 600, fontSize: '0.85rem',
                background: saving ? '#4f46e5aa' : '#6366f1',
                border: 'none', color: '#fff', opacity: (!currentPw || !newPw || !confirmPw) ? 0.5 : 1,
              }}>
                {saving ? 'Saving…' : 'Update Password'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}


// ── Header ───────────────────────────────────────────────────────────────────

interface HeaderProps {
  isHealthy: boolean
  darkMode: boolean
  onToggleSidebar: () => void
  onNavigate: (view: string, workflowId?: string) => void
  user?: CurrentUser | null
  onLogout?: () => void
}

export default function Header({ isHealthy, darkMode, onToggleSidebar, onNavigate, user, onLogout }: HeaderProps) {
  const [dropdownOpen, setDropdownOpen]   = useState(false)
  const [showChangePw, setShowChangePw]   = useState(false)
  const [showHelp,     setShowHelp]       = useState(false)
  const chipRef                           = useRef<HTMLDivElement>(null)

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return
    function handler(e: MouseEvent) {
      if (chipRef.current && !chipRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [dropdownOpen])

  const role = user ? (ROLE_CONFIG[user.role] ?? ROLE_CONFIG['viewer']) : null

  return (
    <>
      <header className={`sticky top-0 z-50 transition-colors duration-300 ${
        darkMode
          ? 'bg-slate-950 border-b border-slate-800'
          : 'bg-white border-b border-gray-200'
      }`}>
        <div className="max-w-full mx-auto px-4 py-1 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            {/* Sidebar Toggle */}
            <button
              onClick={onToggleSidebar}
              className={`p-2 rounded-lg transition-colors duration-200 ${
                darkMode
                  ? 'text-gray-400 hover:bg-slate-800 hover:text-gray-200'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
              title="Toggle sidebar"
            >
              <MenuIcon className="w-6 h-6" />
            </button>

            {/* Logo & Brand */}
            <div className="flex items-center">
              <img src={logo} alt="Axiomatrix IT Operations" style={{ height: '72px', width: 'auto', maxWidth: '320px', objectFit: 'contain' }} />
            </div>
          </div>

          <div className="flex items-center space-x-4">
            {isHealthy ? (
              <div className={`flex items-center space-x-2 text-sm transition-colors ${
                darkMode ? 'text-green-400' : 'text-green-700'
              }`}>
                <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></div>
                <span>API Healthy</span>
              </div>
            ) : (
              <div className={`flex items-center space-x-2 text-sm transition-colors ${
                darkMode ? 'text-red-400' : 'text-red-700'
              }`}>
                <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse"></div>
                <span>API Unavailable</span>
              </div>
            )}

            {/* Notification Bell */}
            <NotificationBell darkMode={darkMode} onNavigate={onNavigate} />

            {/* Help & Docs */}
            <button
              onClick={() => setShowHelp(true)}
              title="Help & Documentation"
              style={{
                width: '32px',
                height: '32px',
                borderRadius: '50%',
                border: darkMode ? '1px solid #334155' : '1px solid #e2e8f0',
                background: darkMode ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.04)',
                color: darkMode ? '#64748b' : '#94a3b8',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.85rem',
                fontWeight: 700,
                transition: 'border-color 0.15s, color 0.15s, background 0.15s',
              }}
              onMouseEnter={e => {
                const el = e.currentTarget
                el.style.borderColor = '#6366f1'
                el.style.color = '#818cf8'
                el.style.background = 'rgba(99,102,241,0.1)'
              }}
              onMouseLeave={e => {
                const el = e.currentTarget
                el.style.borderColor = darkMode ? '#334155' : '#e2e8f0'
                el.style.color = darkMode ? '#64748b' : '#94a3b8'
                el.style.background = darkMode ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.04)'
              }}
            >
              ?
            </button>

            {/* User chip — avatar + name + role badge + dropdown */}
            {user && role && (
              <div ref={chipRef} style={{ position: 'relative' }}>
                {/* Chip button */}
                <button
                  onClick={() => setDropdownOpen(o => !o)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    background: darkMode ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.04)',
                    border: dropdownOpen
                      ? (darkMode ? '1px solid #6366f1' : '1px solid #818cf8')
                      : (darkMode ? '1px solid #2d3748' : '1px solid #e2e8f0'),
                    borderRadius: '10px',
                    padding: '0.3rem 0.55rem 0.3rem 0.3rem',
                    cursor: 'pointer',
                    transition: 'border-color 0.15s',
                  }}
                >
                  {/* Avatar */}
                  <div style={{
                    width: '30px', height: '30px', borderRadius: '50%',
                    background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: '#fff', fontSize: '0.7rem', fontWeight: 700,
                    flexShrink: 0, letterSpacing: '0.02em',
                  }}>
                    {initials(user.name)}
                  </div>

                  {/* Name + role */}
                  <div style={{ lineHeight: 1.2, textAlign: 'left' }}>
                    <div style={{
                      color: darkMode ? '#e2e8f0' : '#1e293b',
                      fontSize: '0.8rem', fontWeight: 600, whiteSpace: 'nowrap',
                    }}>
                      {user.name}
                    </div>
                    <div style={{
                      display: 'inline-block',
                      background: role.bg, color: role.color,
                      fontSize: '0.65rem', fontWeight: 600,
                      padding: '0.05rem 0.4rem', borderRadius: '4px',
                      marginTop: '0.1rem', letterSpacing: '0.03em', textTransform: 'uppercase',
                    }}>
                      {role.label}
                    </div>
                  </div>

                  <IconChevronDown
                    size={13}
                    strokeWidth={2.5}
                    style={{
                      color: darkMode ? '#475569' : '#94a3b8',
                      marginLeft: '0.05rem',
                      transform: dropdownOpen ? 'rotate(180deg)' : 'rotate(0deg)',
                      transition: 'transform 0.18s',
                    }}
                  />
                </button>

                {/* Dropdown */}
                {dropdownOpen && (
                  <div style={{
                    position: 'absolute',
                    top: 'calc(100% + 6px)',
                    right: 0,
                    minWidth: '180px',
                    background: darkMode ? '#1e293b' : '#fff',
                    border: darkMode ? '1px solid #334155' : '1px solid #e2e8f0',
                    borderRadius: '10px',
                    boxShadow: darkMode
                      ? '0 8px 30px rgba(0,0,0,0.5)'
                      : '0 8px 30px rgba(0,0,0,0.12)',
                    padding: '0.35rem',
                    zIndex: 1000,
                  }}>
                    {/* Only show change-password for human accounts (not automation) */}
                    {user.role !== 'automation' && (
                      <DropdownItem
                        icon={<IconKey size={15} strokeWidth={2} />}
                        label="Change Password"
                        darkMode={darkMode}
                        onClick={() => { setDropdownOpen(false); setShowChangePw(true) }}
                      />
                    )}

                    {/* Divider */}
                    <div style={{
                      height: '1px',
                      background: darkMode ? '#334155' : '#f1f5f9',
                      margin: '0.3rem 0',
                    }} />

                    <DropdownItem
                      icon={<IconLogout size={15} strokeWidth={2} />}
                      label="Sign Out"
                      darkMode={darkMode}
                      danger
                      onClick={() => { setDropdownOpen(false); onLogout?.() }}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Self-service change password modal */}
      {showChangePw && (
        <ChangePasswordModal darkMode={darkMode} onClose={() => setShowChangePw(false)} />
      )}

      {/* Help & Docs panel */}
      {showHelp && (
        <HelpDocs onClose={() => setShowHelp(false)} />
      )}
    </>
  )
}

// ── Small helper for dropdown items ─────────────────────────────────────────
interface DropdownItemProps {
  icon: React.ReactNode
  label: string
  darkMode: boolean
  danger?: boolean
  onClick: () => void
}

function DropdownItem({ icon, label, darkMode, danger = false, onClick }: DropdownItemProps) {
  const [hovered, setHovered] = useState(false)

  const baseColor  = danger
    ? (hovered ? '#ef4444' : (darkMode ? '#f87171' : '#dc2626'))
    : (hovered ? (darkMode ? '#e2e8f0' : '#0f172a') : (darkMode ? '#cbd5e1' : '#374151'))

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => e.key === 'Enter' && onClick()}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.55rem',
        padding: '0.45rem 0.75rem',
        cursor: 'pointer',
        fontSize: '0.825rem',
        fontWeight: 500,
        color: baseColor,
        background: hovered
          ? (danger
            ? 'rgba(239,68,68,0.08)'
            : (darkMode ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.05)'))
          : 'transparent',
        borderRadius: '6px',
        transition: 'background 0.1s, color 0.1s',
        userSelect: 'none',
      }}
    >
      {icon}
      {label}
    </div>
  )
}
