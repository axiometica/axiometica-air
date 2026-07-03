import { Handle, Position } from '@xyflow/react';
import type { RunbookStepData, StepType } from '../types';

type NodeProps<T> = { data: T; selected?: boolean; id: string };

// One accent colour per type — everything else is neutral dark
const ACCENT: Record<StepType, string> = {
  start:           '#64748b',
  end:             '#64748b',
  diagnostic:      '#3b82f6',
  action:          '#f59e0b',
  verification:    '#10b981',
  decision:        '#e85d75',
  notify:          '#8b5cf6',
  wait:            '#0ea5e9',
  incident_update: '#22d3ee',
};

const TYPE_LABEL: Record<StepType, string> = {
  start:           'Start',
  end:             'End',
  diagnostic:      'Diagnostic',
  action:          'Action',
  verification:    'Verification',
  decision:        'Decision',
  notify:          'Notify',
  wait:            'Wait',
  incident_update: 'Incident Update',
};

const STATUS_STYLE: Record<string, { bg: string; color: string; text: string }> = {
  pending:  { bg: '#1a1d26', color: '#475569', text: 'Pending' },
  running:  { bg: '#1c1500', color: '#f59e0b', text: 'Running' },
  success:  { bg: '#061a12', color: '#10b981', text: 'Success' },
  skipped:  { bg: '#1a1d26', color: '#334155', text: 'Skipped' },
  failed:   { bg: '#1a0610', color: '#e85d75', text: 'Failed'  },
};

const CARD: React.CSSProperties = {
  background: '#131720',
  borderRadius: 8,
  boxShadow: '0 3px 20px rgba(0,0,0,.6), inset 0 1px 0 rgba(255,255,255,.04)',
};

export function StepNode({ data, selected }: NodeProps<RunbookStepData>) {
  const accent = ACCENT[data.stepType] ?? '#64748b';
  const label  = TYPE_LABEL[data.stepType] ?? data.stepType;
  const status = data.status ? STATUS_STYLE[data.status] : null;
  const isTerminal = data.stepType === 'start' || data.stepType === 'end';
  const isDecision = data.stepType === 'decision';

  const borderColor = data.hasError ? '#f43f5e' : selected ? accent : '#2d3f56';

  // ── Terminal nodes (start / end) ──────────────────────────────────────────
  if (isTerminal) {
    return (
      <div style={{
        ...CARD,
        border: `1.5px solid ${borderColor}`,
        minWidth: 100,
        textAlign: 'center',
        padding: '10px 20px',
        borderLeft: `3px solid ${accent}`,
      }}>
        {data.stepType === 'end' && <Handle type="target" position={Position.Top} style={handle(accent)} />}
        <div style={{ fontSize: 11, fontWeight: 700, color: accent, letterSpacing: '.1em', textTransform: 'uppercase' }}>
          {label}
        </div>
        {data.stepType === 'start' && <Handle type="source" position={Position.Bottom} style={handle(accent)} />}
      </div>
    );
  }

  // ── Decision node ─────────────────────────────────────────────────────────
  if (isDecision) {
    return (
      <div style={{
        ...CARD,
        border: `1.5px solid ${borderColor}`,
        borderLeft: `3px solid ${accent}`,
        minWidth: 200,
        padding: '10px 12px',
      }}>
        <Handle type="target" position={Position.Top} style={handle(accent)} />

        <TypeBadge label={label} color={accent} />

        <div style={{ fontSize: 12, fontFamily: 'monospace', color: '#cbd5e1', margin: '8px 0 10px', wordBreak: 'break-all' }}>
          {data.condition || <span style={{ color: '#334155' }}>click to set condition</span>}
        </div>

        <div style={{ display: 'flex', gap: 6, fontSize: 10 }}>
          <BranchChip label="false" color="#e85d75" bg="#1a0610" />
          <BranchChip label="true" color="#10b981" bg="#061a12" />
        </div>

        <Handle type="source" position={Position.Left}   id="false" style={{ ...handle('#e85d75'), left: -5 }} />
        <Handle type="source" position={Position.Right}  id="true"  style={{ ...handle('#10b981'), right: -5 }} />
        <Handle type="source" position={Position.Bottom} id="default" style={handle(accent)} />
      </div>
    );
  }

  // ── Standard node ─────────────────────────────────────────────────────────
  return (
    <div style={{
      ...CARD,
      border: `1.5px solid ${borderColor}`,
      borderLeft: `3px solid ${accent}`,
      minWidth: 220,
    }}>
      <Handle type="target" position={Position.Top} style={handle(accent)} />

      {/* Header */}
      <div style={{ padding: '9px 12px 7px', borderBottom: '1px solid #1e2a3a', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <TypeBadge label={label} color={accent} />
        {status && (
          <div style={{ fontSize: 10, padding: '2px 7px', borderRadius: 10, background: status.bg, color: status.color, fontWeight: 600 }}>
            {status.text}
          </div>
        )}
      </div>

      {/* Body */}
      <div style={{ padding: '9px 12px 11px' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0', marginBottom: 6 }}>
          {data.name || <span style={{ color: '#334155' }}>Unnamed step</span>}
        </div>

        {data.stepType === 'wait' ? (
          <div style={{ fontSize: 20, fontWeight: 700, color: accent, textAlign: 'center', padding: '4px 0 2px' }}>
            {data.duration_seconds != null && data.duration_seconds > 0
              ? `${data.duration_seconds}s`
              : <span style={{ fontSize: 12, color: '#334155' }}>set duration</span>}
          </div>
        ) : data.stepType === 'verification' ? (
          <>
            {data.metric && <Row label="metric" value={data.metric} />}
            {data.check  && <Row label="check"  value={`${data.check} ${data.value || ''}`} />}
          </>
        ) : data.stepType === 'incident_update' ? (
          <div style={{ fontSize: 16, fontWeight: 700, color: accent, textAlign: 'center', padding: '4px 0 2px', textTransform: 'capitalize' }}>
            {data.state || <span style={{ fontSize: 12, color: '#334155', textTransform: 'none' }}>set state</span>}
          </div>
        ) : (
          <>
            {data.tool   && <Row label="tool"   value={data.tool} />}
            {data.run_if && <Row label="run_if" value={data.run_if} color="#f59e0b" />}
            {data.stepType === 'action' && (data.retry_count ?? 0) > 0 && (
              <div style={{ marginTop: 4, display: 'inline-flex', alignItems: 'center', gap: 4, background: '#1c1500', border: '1px solid #f59e0b44', borderRadius: 10, padding: '2px 8px', fontSize: 10, color: '#f59e0b' }}>
                ↻ retry {data.retry_count}×{data.retry_delay_seconds ? ` / ${data.retry_delay_seconds}s` : ''}
              </div>
            )}
          </>
        )}

        {/* Output capture */}
        {data.outputCapture && Object.keys(data.outputCapture).length > 0 && (
          <div style={{ marginTop: 7, background: '#0d1018', border: '1px solid #1e2a3a', borderRadius: 4, padding: '5px 8px' }}>
            <div style={{ fontSize: 9, color: '#334155', textTransform: 'uppercase', letterSpacing: '.07em', marginBottom: 3 }}>Captures</div>
            {Object.entries(data.outputCapture).map(([k]) => (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, marginBottom: 2 }}>
                <span style={{ width: 4, height: 4, borderRadius: '50%', background: accent, flexShrink: 0, display: 'inline-block' }} />
                <span style={{ fontFamily: 'monospace', color: accent }}>{k}</span>
                {data.liveOutput?.[k] && (
                  <span style={{ color: '#64748b', marginLeft: 'auto' }}>= {data.liveOutput[k]}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <Handle type="source" position={Position.Bottom} style={handle(accent)} />
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function TypeBadge({ label, color }: { label: string; color: string }) {
  return (
    <div style={{ fontSize: 9, fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: '.1em' }}>
      {label}
    </div>
  );
}

function BranchChip({ label, color, bg }: { label: string; color: string; bg: string }) {
  return (
    <div style={{ flex: 1, background: bg, border: `1px solid ${color}30`, borderRadius: 4, padding: '3px 6px', color, fontSize: 10, fontWeight: 600 }}>
      {label} →
    </div>
  );
}

function Row({ label, value, color = '#94a3b8' }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ fontSize: 11, color: '#475569', display: 'flex', gap: 4, marginBottom: 3, flexWrap: 'wrap' }}>
      {label}:&nbsp;<span style={{ fontFamily: 'monospace', fontSize: 10.5, color }}>{value}</span>
    </div>
  );
}

function handle(color: string) {
  return { width: 9, height: 9, background: '#0d1018', border: `2px solid ${color}` };
}
