/**
 * DemoBanner
 *
 * Rendered by App.tsx immediately below the Header when the current user
 * has role='demo'. Signals to the visitor that this is a public demo
 * account with read-only permissions across most of the platform. Kept
 * short so it doesn't eat vertical space on already-dense pages.
 *
 * The banner is purely informational — it does NOT enforce any restriction.
 * All enforcement lives in the backend middleware and the demo-aware Save
 * buttons. If a bug ever exposed the banner to a non-demo user, the worst
 * that happens is they see a message that doesn't apply to them.
 */

interface Props {
  onSignOut?: () => void
}

export default function DemoBanner({ onSignOut }: Props) {
  return (
    <div
      style={{
        // Full-width strip, dark gold/amber to match the "you're in a
        // special mode" feel without alarming. Uses the DS token palette
        // colours so it looks native to the rest of the shell.
        background: '#3a2f0f',
        borderBottom: '1px solid #78350f',
        color: '#fbbf24',
        padding: '10px 20px',
        fontSize: '0.85rem',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 12,
        flexWrap: 'wrap',
      }}
    >
      <span style={{ fontWeight: 600 }}>Demo Mode</span>
      <span style={{ color: '#e8eef5' }}>·</span>
      <span style={{ color: '#e8eef5' }}>
        This is a public read-only demo. Chat is available (20 messages/day).
        Sign in with a real account to make changes.
      </span>
      {onSignOut && (
        <button
          onClick={onSignOut}
          style={{
            marginLeft: 8,
            padding: '3px 10px',
            borderRadius: 4,
            border: '1px solid #78350f',
            background: 'transparent',
            color: '#fbbf24',
            fontSize: '0.78rem',
            cursor: 'pointer',
          }}
        >
          Sign out
        </button>
      )}
    </div>
  )
}
