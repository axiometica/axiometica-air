import type { CSSProperties } from 'react'

interface ActionStep {
  step: number
  action: string
  status: 'pending' | 'in_progress' | 'completed' | 'failed'
  duration: string
  details?: string
}

interface ActionExecutionProps {
  actions: ActionStep[]
  overallStatus: 'pending' | 'in_progress' | 'completed' | 'failed'
}

const CARD: CSSProperties = {
  backgroundColor: '#1a1f2e',
  border: '1px solid #3d4557',
  borderRadius: '10px',
  overflow: 'hidden',
}
const HEADER: CSSProperties = {
  padding: '10px 14px',
  borderBottom: '1px solid #3d4557',
}
const LABEL: CSSProperties = {
  fontSize: '10px',
  fontWeight: 600,
  color: '#a0aec0',
  letterSpacing: '0.07em',
  textTransform: 'uppercase',
}

const stepColor = {
  pending:     '#7a8ba3',
  in_progress: '#3b82f6',
  completed:   '#10b981',
  failed:      '#dc2626',
}

export default function ActionExecution({ actions, overallStatus }: ActionExecutionProps) {
  if (!actions || actions.length === 0) {
    return (
      <div style={CARD}>
        <div style={HEADER}><span style={LABEL}>Diagnostics Steps</span></div>
        <div style={{ padding: '14px' }}>
          <p style={{ fontSize: '12px', color: '#7a8ba3' }}>No diagnostics steps</p>
        </div>
      </div>
    )
  }

  const completedCount   = actions.filter(a => a.status === 'completed').length
  const failedCount      = actions.filter(a => a.status === 'failed').length
  const inProgressCount  = actions.filter(a => a.status === 'in_progress').length
  const progressPercent  = actions.length ? (completedCount / actions.length) * 100 : 0
  const overallColor     = stepColor[overallStatus] || '#a0aec0'

  return (
    <div className="space-y-2">
      {/* Progress overview */}
      <div style={CARD}>
        <div style={HEADER}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={LABEL}>Overall Progress</span>
            <span style={{ fontSize: '11px', fontWeight: 700, color: overallColor }}>
              {completedCount}/{actions.length}
            </span>
          </div>
        </div>
        <div style={{ padding: '12px 14px' }}>
          <div style={{
            height: '5px',
            backgroundColor: '#252c3c',
            border: '1px solid #3d4557',
            borderRadius: '999px',
            overflow: 'hidden',
            marginBottom: '6px',
          }}>
            <div style={{
              height: '100%',
              width: `${progressPercent}%`,
              background: 'linear-gradient(90deg, #3b82f6, #10b981)',
              borderRadius: '999px',
              transition: 'width 0.5s ease',
            }} />
          </div>
          <p style={{ fontSize: '10px', color: '#7a8ba3' }}>{Math.round(progressPercent)}% complete</p>
        </div>
      </div>

      {/* Status counts */}
      <div style={CARD}>
        <div style={{ padding: '12px 14px' }}>
          <div className="grid grid-cols-3 gap-3 text-center">
            {[
              { label: 'Completed',   value: completedCount,  color: '#10b981' },
              { label: 'In Progress', value: inProgressCount, color: '#3b82f6' },
              { label: 'Failed',      value: failedCount,     color: failedCount > 0 ? '#dc2626' : '#7a8ba3' },
            ].map(({ label, value, color }, i) => (
              <div key={label} style={{
                ...(i === 1 ? { borderLeft: '1px solid #3d4557', borderRight: '1px solid #3d4557' } : {}),
              }}>
                <p style={LABEL}>{label}</p>
                <p style={{ fontSize: '22px', fontWeight: 700, color, marginTop: '4px' }}>{value}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

    </div>
  )
}
