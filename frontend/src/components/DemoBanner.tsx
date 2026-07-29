import { useState } from 'react'

interface Props {
  onSignOut?: () => void
}

export default function DemoBanner({ onSignOut }: Props) {
  const [dismissed, setDismissed] = useState(false)

  if (dismissed) return null

  return (
    <div
      style={{
        background: 'rgba(30, 20, 50, 0.92)',
        borderBottom: '1px solid rgba(124, 58, 237, 0.3)',
        color: 'rgba(255, 255, 255, 0.8)',
        padding: '10px 20px',
        fontSize: '0.85rem',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 12,
        flexWrap: 'wrap',
      }}
    >
      <span style={{ fontWeight: 600, color: '#a78bfa' }}>Demo Mode</span>
      <span style={{ opacity: 0.5 }}>·</span>
      <span>
        This is a public read-only demo. For a full demo sign up at{' '}
        <a
          href="https://www.axiometica.com"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#a78bfa', textDecoration: 'underline' }}
        >
          www.axiometica.com
        </a>
        {' '}or get it on{' '}
        <a
          href="https://github.com/axiometica/axiometica-air"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#a78bfa', textDecoration: 'underline' }}
        >
          GitHub
        </a>
      </span>
      <button
        onClick={() => setDismissed(true)}
        style={{
          marginLeft: 8,
          padding: 0,
          border: 'none',
          background: 'transparent',
          color: 'rgba(255, 255, 255, 0.5)',
          fontSize: '1.1rem',
          cursor: 'pointer',
          lineHeight: 1,
        }}
        title="Dismiss"
      >
        ×
      </button>
    </div>
  )
}
