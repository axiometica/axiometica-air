import { useState, useEffect, useRef } from 'react';
import type { StepType } from '../types';
import type { ApprovedAction } from '../App';

const STEP_TYPES: { type: StepType; label: string; sub: string; color: string }[] = [
  { type: 'diagnostic',      label: 'Diagnostic',      sub: 'Read-only check',     color: '#3b82f6' },
  { type: 'action',          label: 'Action',          sub: 'Execute command',     color: '#f59e0b' },
  { type: 'verification',    label: 'Verification',    sub: 'Assert metric',       color: '#10b981' },
  { type: 'decision',        label: 'Decision',        sub: 'Branch on output',    color: '#e85d75' },
  { type: 'notify',          label: 'Notify',          sub: 'Alert / webhook',     color: '#8b5cf6' },
  { type: 'wait',            label: 'Wait',            sub: 'Pause execution',     color: '#0ea5e9' },
  { type: 'incident_update', label: 'Incident Update', sub: 'Set resolution state', color: '#22d3ee' },
];

const CATEGORY_COLOR: Record<string, string> = {
  diagnostic:            '#3b82f6',
  remediation_safe:      '#10b981',
  remediation_intrusive: '#f59e0b',
};

const CATEGORY_TO_STEP_TYPE: Record<string, StepType> = {
  diagnostic:            'diagnostic',
  remediation_safe:      'action',
  remediation_intrusive: 'action',
};

const FALLBACK_TOOLS: { tool: string; type: StepType; color: string }[] = [
  { tool: 'check_disk_usage', type: 'diagnostic', color: '#3b82f6' },
  { tool: 'check_memory',     type: 'diagnostic', color: '#3b82f6' },
  { tool: 'get_logs',         type: 'diagnostic', color: '#3b82f6' },
  { tool: 'cleanup_logs',     type: 'action',     color: '#f59e0b' },
  { tool: 'restart_service',  type: 'action',     color: '#f59e0b' },
  { tool: 'rotate_logs',      type: 'action',     color: '#f59e0b' },
];

// ── Taxonomy domain → accent colour ──────────────────────────────────────────
const DOMAIN_COLORS: Record<string, string> = {
  infrastructure: '#f59e0b',
  container:      '#3b82f6',
  application:    '#10b981',
  database:       '#8b5cf6',
  cloud:          '#0ea5e9',
  network:        '#06b6d4',
  security:       '#ef4444',
  log:            '#f97316',
  synthetic:      '#ec4899',
  custom:         '#94a3b8',
};

// ── EventType taxonomy option ─────────────────────────────────────────────────
interface EventTypeOption {
  code: string;
  label: string;
  category: string;
}

// ── Searchable taxonomy combobox ──────────────────────────────────────────────
export function EventTypeCombobox({
  value,
  onChange,
  error,
}: {
  value: string;
  onChange: (v: string) => void;
  error?: boolean;
}) {
  const [options, setOptions]   = useState<EventTypeOption[]>([]);
  const [query,   setQuery]     = useState(value);
  const [open,    setOpen]      = useState(false);
  const [cursor,  setCursor]    = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Fetch taxonomy once on mount
  useEffect(() => {
    const tok  = localStorage.getItem('ap_token') || '';
    const hdrs: Record<string, string> = tok ? { Authorization: `Bearer ${tok}` } : {};
    fetch('/api/event-types?enabled_only=true', { headers: hdrs })
      .then(r => (r.ok ? r.json() : []))
      .then((data: EventTypeOption[]) => setOptions(Array.isArray(data) ? data : []))
      .catch(() => {/* taxonomy unavailable — degrade gracefully */});
  }, []);

  // Keep local query in sync when the parent changes `value` (e.g. loading a saved runbook)
  useEffect(() => { setQuery(value); }, [value]);

  // Close dropdown on outside click and commit current query
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
        if (query !== value) onChange(query.trim());
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [query, value, onChange]);

  // Filter options
  const q        = query.toLowerCase().trim();
  const filtered = (q
    ? options.filter(
        o =>
          o.code.includes(q) ||
          o.label.toLowerCase().includes(q) ||
          o.category.startsWith(q),
      )
    : options
  ).slice(0, 15);

  const select = (code: string) => {
    setQuery(code);
    onChange(code);
    setOpen(false);
    setCursor(-1);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open) { setOpen(true); return; }
    switch (e.key) {
      case 'ArrowDown':
        setCursor(c => Math.min(c + 1, filtered.length - 1));
        e.preventDefault();
        break;
      case 'ArrowUp':
        setCursor(c => Math.max(c - 1, 0));
        e.preventDefault();
        break;
      case 'Enter':
        if (cursor >= 0 && filtered[cursor]) {
          select(filtered[cursor].code);
          e.preventDefault();
        } else {
          // Commit free-text entry
          onChange(query.trim());
          setOpen(false);
        }
        break;
      case 'Escape':
        setOpen(false);
        setCursor(-1);
        break;
    }
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <input
        value={query}
        onChange={e => {
          setQuery(e.target.value);
          setOpen(true);
          setCursor(-1);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder="Search event types…"
        style={{ ...inputSt, fontFamily: 'monospace', fontSize: 10, ...(error ? { border: '1px solid #ef4444' } : {}) }}
        spellCheck={false}
        autoComplete="off"
      />

      {open && filtered.length > 0 && (
        <div
          style={{
            position: 'absolute',
            zIndex: 9999,
            left: 0,
            right: 0,
            top: 'calc(100% + 2px)',
            background: '#0d1018',
            border: '1px solid #2a3548',
            borderRadius: 6,
            maxHeight: 260,
            overflowY: 'auto',
            boxShadow: '0 8px 24px rgba(0,0,0,.6)',
          }}
        >
          {filtered.map((opt, i) => {
            const accent = DOMAIN_COLORS[opt.category] ?? '#94a3b8';
            return (
              <div
                key={opt.code}
                onMouseDown={() => select(opt.code)}
                style={{
                  padding: '5px 8px',
                  cursor: 'pointer',
                  background: i === cursor ? '#1e2a3a' : 'transparent',
                  borderBottom: '1px solid #1a2030',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  {/* domain badge */}
                  <span
                    style={{
                      fontSize: 8,
                      padding: '1px 5px',
                      borderRadius: 3,
                      background: accent + '28',
                      color: accent,
                      fontWeight: 700,
                      flexShrink: 0,
                      letterSpacing: '.05em',
                      textTransform: 'uppercase',
                    }}
                  >
                    {opt.category}
                  </span>
                  <span style={{ fontSize: 11, color: '#cbd5e1', fontWeight: 500, lineHeight: 1.2 }}>
                    {opt.label}
                  </span>
                </div>
                <div
                  style={{
                    fontFamily: 'monospace',
                    fontSize: 9,
                    color: '#475569',
                    marginTop: 1,
                    paddingLeft: 2,
                  }}
                >
                  {opt.code}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Sidebar props ─────────────────────────────────────────────────────────────

interface SidebarProps {
  runbookName: string;
  triggerType: string;
  onNameChange: (v: string) => void;
  onTriggerChange: (v: string) => void;
  description: string;
  onDescriptionChange: (v: string) => void;
  platform: string;
  onPlatformChange: (v: string) => void;
  service: string;
  onServiceChange: (v: string) => void;
  blastRadius: number;
  onBlastRadiusChange: (v: number) => void;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  nodeCount: number;
  edgeCount: number;
  approvedActions: ApprovedAction[];
  // Validation state — set after Validate / Save is attempted
  nameError?: boolean;
  eventTypeError?: boolean;
  noActionError?: boolean;
}

export function Sidebar({
  runbookName,
  triggerType,
  onNameChange,
  onTriggerChange,
  description,
  onDescriptionChange,
  platform,
  onPlatformChange,
  service,
  onServiceChange,
  blastRadius,
  onBlastRadiusChange,
  enabled,
  onEnabledChange,
  nodeCount,
  edgeCount,
  approvedActions,
  nameError,
  eventTypeError,
  noActionError,
}: SidebarProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);

  const onDragStart = (e: React.DragEvent, type: StepType, tool?: string) => {
    e.dataTransfer.setData('stepType', type);
    if (tool) e.dataTransfer.setData('tool', tool);
    e.dataTransfer.effectAllowed = 'move';
  };

  const quickTools =
    approvedActions.length > 0
      ? approvedActions
          .filter(a => a.enabled)
          .slice(0, 10)
          .map(a => ({
            tool:        a.tool_name,
            type:        CATEGORY_TO_STEP_TYPE[a.category] ?? ('action' as StepType),
            color:       CATEGORY_COLOR[a.category] ?? '#94a3b8',
            description: a.name,
          }))
      : FALLBACK_TOOLS.map(t => ({ ...t, description: '' }));


  return (
    <div
      style={{
        width: 210,
        background: '#0d1018',
        borderRight: '1px solid #1e2a3a',
        display: 'flex',
        flexDirection: 'column',
        overflowY: 'auto',
        flexShrink: 0,
      }}
    >
      {/* ── Runbook identity ── */}
      <div style={{ padding: '12px 12px 10px', borderBottom: '1px solid #1e2a3a' }}>
        <SectionLabel>
          Runbook Name{' '}
          <span style={{ color: '#ef4444' }}>*</span>
        </SectionLabel>
        <input
          value={runbookName}
          onChange={e => onNameChange(e.target.value)}
          placeholder="e.g. Disk Full"
          style={{ ...inputSt, ...(nameError ? { border: '1px solid #ef4444' } : {}) }}
        />
        {nameError && (
          <div style={{ fontSize: 10, color: '#ef4444', marginTop: 3 }}>Name is required</div>
        )}

        <SectionLabel style={{ marginTop: 10 }}>
          Event Type{' '}
          <span style={{ color: '#ef4444' }}>*</span>
        </SectionLabel>
        <EventTypeCombobox value={triggerType} onChange={onTriggerChange} error={eventTypeError} />
        {eventTypeError && (
          <div style={{ fontSize: 10, color: '#ef4444', marginTop: 3 }}>Event type is required</div>
        )}
        {!eventTypeError && triggerType && (
          <div style={{ fontSize: 9, color: '#334155', marginTop: 3, fontFamily: 'monospace',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {triggerType}
          </div>
        )}

        {noActionError && (
          <div style={{
            marginTop: 8,
            padding: '6px 8px',
            background: 'rgba(239,68,68,0.08)',
            border: '1px solid rgba(239,68,68,0.3)',
            borderRadius: 5,
            fontSize: 10,
            color: '#ef4444',
          }}>
            At least one Action step is required
          </div>
        )}

        <SectionLabel style={{ marginTop: 10 }}>Description</SectionLabel>
        <textarea
          value={description}
          onChange={e => onDescriptionChange(e.target.value)}
          placeholder="What does this runbook do?"
          rows={2}
          style={{
            ...inputSt,
            resize: 'vertical',
            fontSize: 11,
            lineHeight: 1.4,
            minHeight: 44,
          }}
        />

        {/* ── Settings collapsible ── */}
        <button
          onClick={() => setSettingsOpen(o => !o)}
          style={{
            marginTop: 10,
            width: '100%',
            background: 'transparent',
            border: '1px solid #1e2a3a',
            borderRadius: 5,
            color: '#475569',
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: '.06em',
            textTransform: 'uppercase',
            padding: '5px 8px',
            cursor: 'pointer',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span>Settings</span>
          <span style={{ fontSize: 8 }}>{settingsOpen ? '▲' : '▼'}</span>
        </button>

        {settingsOpen && (
          <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 10 }}>

            {/* Platform */}
            <div>
              <SectionLabel>Platform</SectionLabel>
              <select
                value={platform}
                onChange={e => onPlatformChange(e.target.value)}
                style={{ ...inputSt, fontSize: 11 }}
              >
                <option value="any">any</option>
                <option value="docker">docker</option>
                <option value="kubernetes">kubernetes</option>
                <option value="linux">linux</option>
                <option value="windows">windows</option>
              </select>
            </div>

            {/* Service scope */}
            <div>
              <SectionLabel>Service</SectionLabel>
              <input
                value={service}
                onChange={e => onServiceChange(e.target.value)}
                placeholder="blank = catch-all"
                style={{ ...inputSt, fontSize: 11 }}
              />
              <div style={{ fontSize: 9, color: '#334155', marginTop: 3 }}>
                Narrow this runbook to a specific service
              </div>
            </div>

            {/* Risk level */}
            <div>
              <SectionLabel>Risk Level</SectionLabel>
              <select
                value={blastRadius}
                onChange={e => onBlastRadiusChange(parseInt(e.target.value))}
                style={{ ...inputSt, fontSize: 11 }}
              >
                <option value={1}>1 — Safe (read-only)</option>
                <option value={2}>2 — Low (graceful ops)</option>
                <option value={3}>3 — Moderate (restart/scale)</option>
                <option value={4}>4 — High (data impact)</option>
                <option value={5}>5 — Critical (destructive)</option>
              </select>
              <div style={{ fontSize: 9, color: '#334155', marginTop: 3 }}>
                Governs approval requirements in policy rules
              </div>
            </div>

            {/* Enabled toggle */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <SectionLabel style={{ marginBottom: 0 }}>Enabled</SectionLabel>
              <div
                onClick={() => onEnabledChange(!enabled)}
                style={{
                  width: 34,
                  height: 18,
                  borderRadius: 9,
                  background: enabled ? '#3b82f6' : '#2a3548',
                  position: 'relative',
                  cursor: 'pointer',
                  transition: 'background .2s',
                  flexShrink: 0,
                }}
              >
                <div style={{
                  position: 'absolute',
                  top: 2,
                  left: enabled ? 16 : 2,
                  width: 14,
                  height: 14,
                  borderRadius: '50%',
                  background: '#fff',
                  transition: 'left .2s',
                }} />
              </div>
            </div>

          </div>
        )}
      </div>

      {/* ── Step palette ── */}
      <div style={{ padding: '10px 12px 4px' }}>
        <SectionLabel>Add Step</SectionLabel>
        {STEP_TYPES.map(s => (
          <div key={s.type} draggable onDragStart={e => onDragStart(e, s.type)} style={tile(s.color)}>
            <span style={{ width: 3, height: 28, borderRadius: 2, background: s.color, flexShrink: 0, display: 'inline-block' }} />
            <div>
              <div style={{ fontSize: 12, color: '#cbd5e1', fontWeight: 500 }}>{s.label}</div>
              <div style={{ fontSize: 10, color: '#475569' }}>{s.sub}</div>
            </div>
          </div>
        ))}
      </div>

      {/* ── Quick tools ── */}
      <div style={{ padding: '8px 12px 4px', flex: 1 }}>
        <SectionLabel>
          {approvedActions.length > 0 ? `Tools (${approvedActions.length})` : 'Quick Tools'}
        </SectionLabel>
        {quickTools.map(q => (
          <div key={q.tool} draggable onDragStart={e => onDragStart(e, q.type, q.tool)} style={tile(q.color)}>
            <span style={{ width: 3, height: 22, borderRadius: 2, background: q.color, flexShrink: 0, display: 'inline-block' }} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 11, color: '#94a3b8', fontFamily: 'monospace',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {q.tool}
              </div>
              <div style={{ fontSize: 10, color: '#334155' }}>{q.description || q.type}</div>
            </div>
          </div>
        ))}
        {approvedActions.length > 10 && (
          <div style={{ fontSize: 10, color: '#334155', padding: '4px 2px' }}>
            + {approvedActions.length - 10} more — search in properties panel
          </div>
        )}
      </div>

      {/* ── Stats ── */}
      <div style={{ padding: 12, borderTop: '1px solid #1e2a3a' }}>
        <div style={{ background: '#161b28', border: '1px solid #2a3548', borderRadius: 6, padding: '9px 11px' }}>
          <SectionLabel style={{ marginBottom: 6 }}>Graph</SectionLabel>
          <StatRow label="Nodes" value={nodeCount} />
          <StatRow label="Edges" value={edgeCount} />
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionLabel({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        fontSize: 9,
        fontWeight: 700,
        color: '#334155',
        textTransform: 'uppercase',
        letterSpacing: '.1em',
        marginBottom: 7,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: number }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        fontSize: 11,
        color: '#475569',
        marginBottom: 2,
      }}
    >
      {label} <span style={{ color: '#64748b' }}>{value}</span>
    </div>
  );
}

const inputSt: React.CSSProperties = {
  width: '100%',
  background: '#161b28',
  border: '1px solid #2a3548',
  borderRadius: 5,
  padding: '6px 8px',
  fontSize: 12,
  color: '#e2e8f0',
  outline: 'none',
  boxSizing: 'border-box',
};

function tile(_color: string): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: 9,
    padding: '7px 9px',
    borderRadius: 6,
    marginBottom: 4,
    border: '1px solid #2a3548',
    cursor: 'grab',
    background: '#161b28',
  };
}
