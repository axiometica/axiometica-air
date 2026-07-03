interface ConsoleOutputProps {
  step: number
  tool: string
  command: string
  rawOutput?: string   // captured stdout/stderr from the real process
  output: string       // interpreted/summary message
  duration?: string
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped'
  runIf?: string       // condition string shown when step was skipped
}

const statusColor: Record<string, string> = {
  pending:     '#7a8ba3',
  in_progress: '#3b82f6',
  completed:   '#10b981',
  failed:      '#dc2626',
  skipped:     '#6b7a99',
}
const statusSymbol: Record<string, string> = {
  pending:     '◯',
  in_progress: '⟳',
  completed:   '✓',
  failed:      '✕',
  skipped:     '⏭',
}

const sectionLabel: React.CSSProperties = {
  fontSize: '9px',
  fontWeight: 700,
  letterSpacing: '0.1em',
  textTransform: 'uppercase' as const,
  color: '#4b6e8a',
  marginBottom: '4px',
  fontFamily: 'Monaco, "Courier New", Consolas, monospace',
}

export default function ConsoleOutput({ step, tool, command, rawOutput, output, duration, status, runIf }: ConsoleOutputProps) {
  const color   = statusColor[status]  ?? '#7a8ba3'
  const symbol  = statusSymbol[status] ?? '◯'
  const skipped = status === 'skipped'

  return (
    <div style={{
      backgroundColor: skipped ? '#161a26' : '#1a1f2e',
      border: `1px solid ${skipped ? '#2a3147' : '#3d4557'}`,
      borderLeft: `3px solid ${color}`,
      borderRadius: '0 8px 8px 0',
      overflow: 'hidden',
      marginBottom: '4px',
      opacity: skipped ? 0.65 : 1,
    }}>
      {/* Step header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '8px 12px',
        borderBottom: skipped ? 'none' : '1px solid #3d4557',
        backgroundColor: skipped ? '#1a1f2e' : '#252c3c',
      }}>
        <span style={{ fontSize: '13px', color, fontWeight: 700 }}>{symbol}</span>
        <span style={{
          fontSize: '11px', fontWeight: 600,
          color: skipped ? '#6b7a99' : '#e8eef5',
          fontStyle: skipped ? 'italic' : 'normal',
        }}>
          Step {step}:{' '}
          <span style={{ fontFamily: 'monospace', color: skipped ? '#4b5a7a' : '#a0aec0' }}>{tool}</span>
        </span>
        {skipped && (
          <span style={{
            fontSize: '9px', fontWeight: 600, color: '#6b7a99',
            backgroundColor: '#1e2535', border: '1px solid #2a3147',
            borderRadius: '4px', padding: '1px 6px', letterSpacing: '0.06em',
          }}>
            SKIPPED
          </span>
        )}
        {duration && !skipped && (
          <span style={{ fontSize: '10px', color: '#7a8ba3', marginLeft: 'auto', fontFamily: 'monospace' }}>
            {duration}
          </span>
        )}
      </div>

      {/* Skipped step: show condition inline, no console body */}
      {skipped && runIf && (
        <div style={{
          padding: '6px 12px 8px 12px',
          fontFamily: 'Monaco, "Courier New", Consolas, monospace',
          fontSize: '10px',
          color: '#4b5a7a',
        }}>
          <span style={{ color: '#3a4a6a' }}>condition: </span>
          <span style={{ color: '#5a6a8a', fontStyle: 'italic' }}>{runIf}</span>
        </div>
      )}

      {/* Console window — hidden for skipped steps */}
      {!skipped && (
        <div style={{
          backgroundColor: '#0a0e14',
          fontFamily: 'Monaco, "Courier New", Consolas, monospace',
          fontSize: '11px',
          lineHeight: 1.55,
          padding: '12px 14px',
          maxHeight: '320px',
          overflowY: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}>
          {/* ── Section 1: Command ── */}
          <div style={{ marginBottom: rawOutput || output ? '12px' : '0' }}>
            <div style={sectionLabel}>command</div>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              <span style={{ color: '#4b6e8a', userSelect: 'none' }}>$</span>
              <span style={{ color: '#00d084' }}>{command || '(no command recorded)'}</span>
            </div>
          </div>

          {/* ── Section 2: Raw output (stdout/stderr) ── */}
          {rawOutput && (
            <div style={{ marginBottom: output ? '12px' : '0' }}>
              <div style={sectionLabel}>raw output</div>
              <div style={{ color: '#8baec0' }}>{rawOutput}</div>
            </div>
          )}

          {/* ── Section 3: Interpreted summary ── */}
          {output && (
            <div style={{ marginBottom: runIf ? '12px' : '0' }}>
              <div style={sectionLabel}>interpreted</div>
              <div style={{ color: '#a8c4d8', fontStyle: 'italic' }}>
                {output}
              </div>
            </div>
          )}

          {/* ── Section 4: run_if condition (when step ran conditionally) ── */}
          {runIf && (
            <div>
              <div style={sectionLabel}>condition</div>
              <div style={{ color: '#4b6e8a', fontStyle: 'italic' }}>{runIf}</div>
            </div>
          )}

          {!rawOutput && !output && status === 'pending' && (
            <div style={{ color: '#4b6e8a', marginTop: '4px' }}>(waiting for execution…)</div>
          )}
        </div>
      )}
    </div>
  )
}
