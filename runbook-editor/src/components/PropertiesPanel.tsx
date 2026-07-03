import { useState, useRef, useEffect } from 'react';
import type { Node, Edge } from '@xyflow/react';
import type { RunbookStepData } from '../types';
import type { ApprovedAction } from '../App';

// ── Auto-parsed outputs per tool (from backend _parse_tool_output) ────────────
// These are available as step_N.variable_name without any output_capture config.
// Resolution order: catalog-defined output_fields (ApprovedAction.output_fields, set via
// the Approved Action editor) → this static fallback map, kept for tools not yet migrated
// off the legacy hardcoded parser in ToolRegistryAgent._parse_tool_output.
const TOOL_AUTO_OUTPUTS: Record<string, Array<{ name: string; type: string; desc: string }>> = {
  check_disk_usage:      [{ name: 'disk_percent',       type: 'number', desc: '% disk used' },
                          { name: 'available',          type: 'string', desc: 'Free space (e.g. "13G")' }],
  host_disk_usage:       [{ name: 'disk_percent',       type: 'number', desc: '% disk used' },
                          { name: 'available',          type: 'string', desc: 'Free space' }],
  check_memory:          [{ name: 'mem_percent',        type: 'number', desc: '% memory used' },
                          { name: 'mem_used_gb',        type: 'number', desc: 'GB in use' },
                          { name: 'mem_available_gb',   type: 'number', desc: 'GB available' },
                          { name: 'mem_total_gb',       type: 'number', desc: 'GB total' }],
  check_cpu:             [{ name: 'cpu_percent',        type: 'number', desc: '% CPU (user+sys)' },
                          { name: 'cpu_user_percent',   type: 'number', desc: '% user CPU' },
                          { name: 'cpu_sys_percent',    type: 'number', desc: '% sys CPU' }],
  get_error_rate:        [{ name: 'error_count',        type: 'number', desc: 'ERROR/WARN lines in window' },
                          { name: 'has_errors',         type: 'bool',   desc: 'true when error_count > 0' }],
  check_health_endpoint: [{ name: 'http_code',          type: 'number', desc: 'HTTP status code' },
                          { name: 'healthy',            type: 'bool',   desc: 'true when code < 400' }],
  ping_service:          [{ name: 'http_code',          type: 'number', desc: 'HTTP status code' },
                          { name: 'reachable',          type: 'bool',   desc: 'true when code < 500' }],
  top_processes:         [{ name: 'top_process',        type: 'string', desc: 'Highest-CPU process name' },
                          { name: 'top_cpu_percent',    type: 'number', desc: '% CPU of top process' },
                          { name: 'top_mem_percent',    type: 'number', desc: '% mem of top process' }],
  host_top_processes:    [{ name: 'top_process',        type: 'string', desc: 'Highest-CPU process name' },
                          { name: 'top_cpu_percent',    type: 'number', desc: '% CPU of top process' }],
  host_service_status:   [{ name: 'service_status',    type: 'string', desc: 'running | stopped | failed' },
                          { name: 'service_running',   type: 'bool',   desc: 'true when running' }],
  check_swap:            [{ name: 'swap_percent',       type: 'number', desc: '% swap used' },
                          { name: 'swap_used_kb',       type: 'number', desc: 'KB swap in use' }],
  query_metrics:         [{ name: 'metric_value',       type: 'number', desc: 'Prometheus metric value' }],
  check_container_status: [
    { name: 'container_status',        type: 'string', desc: 'created | running | paused | restarting | removing | exited | dead' },
    { name: 'container_running',       type: 'bool',   desc: 'true when container_status is running' },
    { name: 'container_restart_count', type: 'number', desc: 'Docker restart count' },
    { name: 'container_health',        type: 'string', desc: 'none | starting | healthy | unhealthy (none = no healthcheck defined)' },
    { name: 'container_exit_code',     type: 'number', desc: 'Last exit code — 0 = clean' },
  ],
};

function getAutoOutputs(tool: string, approvedActions: ApprovedAction[]): Array<{ name: string; type: string; desc: string }> {
  const action = approvedActions.find(a => a.tool_name === tool);
  if (action?.output_fields && action.output_fields.length > 0) {
    return action.output_fields.map(f => ({
      name: f.field,
      type: f.type === 'integer' || f.type === 'float' ? 'number' : f.type === 'boolean' ? 'bool' : 'string',
      desc: `${f.kind === 'jsonpath' ? 'JSONPath' : 'regex'}: ${f.pattern}`,
    }));
  }
  return TOOL_AUTO_OUTPUTS[tool] || [];
}

// ── Condition validation ──────────────────────────────────────────────────────
// Catches the exact bug class that motivated this: a condition referencing a
// step_id.field pair where step_id doesn't exist, or field isn't actually
// produced by that step's tool (e.g. a stale/copy-pasted variable name) — and,
// for the AI generator's bare-name style (e.g. "has_deadlock == true", no step_id
// prefix), a variable that no step in the runbook produces at all.
//
// Only the LEFT-HAND SIDE of each comparison is ever treated as a variable
// reference — the right-hand side is a literal value (quoted or unquoted string,
// number, true/false) and must never be flagged, since the backend's own
// evaluator accepts unquoted RHS strings too (e.g. "container_status == running").
//
// Steps whose tool has no known output metadata are skipped, not flagged —
// silence is safer than a false positive when we simply don't know yet.
function validateCondition(
  condition: string,
  allNodes: Node<RunbookStepData>[],
  approvedActions: ApprovedAction[],
): string[] {
  if (!condition.trim()) return [];
  const warnings: string[] = [];
  const seen = new Set<string>();

  // Every variable name produced anywhere in the runbook — via output_capture aliases
  // or a step's tool's native auto-parsed fields. Mirrors the backend's bare-name
  // resolver, which scans every captured step's output dict for a literal key match
  // regardless of which step produced it.
  const allKnownVars = new Set<string>();
  for (const n of allNodes) {
    for (const k of Object.keys(n.data.outputCapture || {})) allKnownVars.add(k);
    if (n.data.tool) {
      for (const f of getAutoOutputs(n.data.tool, approvedActions)) allKnownVars.add(f.name);
    }
  }

  // Split into individual comparison clauses the same way the backend does (&& / and / || / or),
  // then pull just the LHS token out of each.
  const clauses = condition.split(/\s*&&\s*|\s+and\s+|\s*\|\|\s*|\s+or\s+/i);
  for (const raw of clauses) {
    const clause = raw.trim();
    if (!clause) continue;
    const opMatch = clause.match(/^(\S+?)\s*(==|!=|>=|<=|>|<|\bIN\b|\bNOT\s+IN\b)/i);
    const token = opMatch ? opMatch[1] : (/^[A-Za-z_]\w*(\.\w+)?$/.test(clause) ? clause : null);
    if (!token || seen.has(token)) continue;
    seen.add(token);

    const dotted = token.match(/^([A-Za-z_]\w*)\.(\w+)$/);
    if (dotted) {
      const [, stepId, field] = dotted;
      const node = allNodes.find(n => n.id === stepId);
      if (!node) {
        warnings.push(`"${stepId}" is not a step in this runbook`);
        continue;
      }
      const tool = node.data.tool || '';
      if (!tool) continue;
      const known = getAutoOutputs(tool, approvedActions);
      if (known.length === 0) continue;
      if (!known.find(k => k.name === field)) {
        warnings.push(`"${field}" is not produced by ${tool} — known: ${known.map(k => k.name).join(', ')}`);
      }
    } else if (/^[A-Za-z_]\w*$/.test(token)) {
      if (!allKnownVars.has(token)) {
        warnings.push(`"${token}" is not produced by any step in this runbook`);
      }
    }
  }
  return warnings;
}

const CATEGORY_COLOR: Record<string, string> = {
  diagnostic:            '#3b82f6',
  remediation_safe:      '#10b981',
  remediation_intrusive: '#f59e0b',
};
const CATEGORY_LABEL: Record<string, string> = {
  diagnostic:            'diag',
  remediation_safe:      'safe',
  remediation_intrusive: 'risky',
};

interface Props {
  node: { id: string; data: RunbookStepData } | null;
  allNodeIds: string[];
  allNodes: Node<RunbookStepData>[];
  edges: Edge[];
  onChange: (id: string, data: Partial<RunbookStepData>) => void;
  onDelete: (id: string) => void;
  approvedActions: ApprovedAction[];
}

// ── Notification team combobox (for the "team" arg on notify/alert_* steps) ───
// Free-text autocomplete, not a strict select — the value is only resolved
// against the live registry at execution time (case-insensitive), and falls
// back to the default channels if it doesn't match, so an unrecognised or
// not-yet-created name is still a valid, if no-op, entry.

function NotificationTeamArgCombobox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [teams, setTeams] = useState<{ name: string; enabled: boolean }[]>([]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const tok = localStorage.getItem('ap_token') || '';
    const hdrs: Record<string, string> = tok ? { Authorization: `Bearer ${tok}` } : {};
    fetch('/api/notification-teams', { headers: hdrs })
      .then(r => (r.ok ? r.json() : []))
      .then((d: any[]) => setTeams(Array.isArray(d) ? d.map(t => ({ name: t.name, enabled: t.enabled })) : []))
      .catch(() => {});
  }, []);

  useEffect(() => { setQuery(value); }, [value]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as unknown as globalThis.Node)) {
        setOpen(false);
        onChange(query);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [query, onChange]);

  const q = query.toLowerCase();
  const filtered = teams.filter(t => !q || t.name.toLowerCase().includes(q)).slice(0, 12);

  return (
    <div ref={containerRef} style={{ position: 'relative', flex: 1 }}>
      <input
        style={{ ...inputSt, fontFamily: 'monospace', fontSize: 10.5, width: '100%' }}
        value={query}
        onChange={e => { setQuery(e.target.value); onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        placeholder="team name…"
      />
      {open && (
        <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 200, background: '#161b28', border: '1px solid #2a3548', borderRadius: 6, boxShadow: '0 8px 24px rgba(0,0,0,.5)', maxHeight: 200, overflowY: 'auto', marginTop: 2 }}>
          {teams.length === 0 ? (
            <div style={{ padding: '8px 11px', fontSize: 10.5, color: '#4a5068' }}>
              No notification teams configured yet (Settings → Notification Teams)
            </div>
          ) : filtered.length === 0 ? (
            <div style={{ padding: '8px 11px', fontSize: 10.5, color: '#4a5068' }}>No matching teams</div>
          ) : (
            filtered.map(t => (
              <div key={t.name} onMouseDown={() => { setQuery(t.name); onChange(t.name); setOpen(false); }}
                style={{ padding: '7px 11px', cursor: 'pointer', borderBottom: '1px solid #1e2537', display: 'flex', alignItems: 'center', gap: 6 }}
                onMouseEnter={e => (e.currentTarget.style.background = '#1e2537')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#e2e8f0' }}>{t.name}</span>
                {!t.enabled && <span style={{ fontSize: 9, color: '#f59e0b' }}>disabled</span>}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── Searchable tool combobox ──────────────────────────────────────────────────

function ToolCombobox({
  value, onChange, onSelect, approvedActions,
}: {
  value: string;
  onChange: (v: string) => void;
  onSelect: (action: ApprovedAction) => void;
  approvedActions: ApprovedAction[];
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setQuery(value); }, [value]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as unknown as globalThis.Node)) {
        setOpen(false);
        onChange(query);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [query, onChange]);

  const filtered = approvedActions.filter(a =>
    !query || a.tool_name.includes(query) || a.name.toLowerCase().includes(query.toLowerCase()) || (a.description || '').toLowerCase().includes(query.toLowerCase())
  ).slice(0, 30);

  const handleSelect = (a: ApprovedAction) => {
    setQuery(a.tool_name);
    onChange(a.tool_name);
    onSelect(a);
    setOpen(false);
  };

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <input
          style={{ ...inputSt, fontFamily: 'monospace', flex: 1, borderRadius: '5px 0 0 5px' }}
          value={query}
          onChange={e => { setQuery(e.target.value); onChange(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          placeholder="search tools…"
        />
        <button type="button" onClick={() => setOpen(o => !o)}
          style={{ background: '#1e2537', border: '1px solid #2a3548', borderLeft: 'none', borderRadius: '0 5px 5px 0', padding: '6px 7px', cursor: 'pointer', color: '#4a5068', lineHeight: 1, fontSize: 11 }}>
          ▾
        </button>
      </div>
      {open && (
        <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 200, background: '#161b28', border: '1px solid #2a3548', borderRadius: 6, boxShadow: '0 8px 24px rgba(0,0,0,.5)', maxHeight: 280, overflowY: 'auto', marginTop: 2 }}>
          {filtered.length === 0 ? (
            <div style={{ padding: '10px 12px', fontSize: 11, color: '#4a5068' }}>
              {approvedActions.length === 0 ? 'Loading tools…' : 'No matching tools'}
            </div>
          ) : (
            filtered.map(a => (
              <div key={a.tool_name} onMouseDown={() => handleSelect(a)}
                style={{ padding: '8px 11px', cursor: 'pointer', borderBottom: '1px solid #1e2537', display: 'flex', alignItems: 'flex-start', gap: 8 }}
                onMouseEnter={e => (e.currentTarget.style.background = '#1e2537')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 1 }}>
                    <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#e2e8f0' }}>{a.tool_name}</span>
                    <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, color: CATEGORY_COLOR[a.category] || '#7c85a0', background: `${CATEGORY_COLOR[a.category] || '#7c85a0'}20`, border: `1px solid ${CATEGORY_COLOR[a.category] || '#7c85a0'}40`, textTransform: 'uppercase', letterSpacing: '.05em' }}>
                      {CATEGORY_LABEL[a.category] || a.category}
                    </span>
                  </div>
                  {a.description && <div style={{ fontSize: 10, color: '#7c85a0', lineHeight: 1.4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.description}</div>}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── BFS: find node IDs reachable from 'start' before hitting the decision node ─

function getUpstreamIds(decisionId: string, edges: Edge[]): Set<string> {
  const adj = new Map<string, string[]>();
  edges.forEach(e => {
    if (!adj.has(e.source)) adj.set(e.source, []);
    adj.get(e.source)!.push(e.target);
  });

  const upstream = new Set<string>();
  const queue    = ['start'];
  const visited  = new Set<string>(['start']);

  while (queue.length) {
    const curr = queue.shift()!;
    if (curr === decisionId) continue; // stop here — don't traverse through decision
    upstream.add(curr);
    for (const next of (adj.get(curr) ?? [])) {
      if (!visited.has(next) && next !== decisionId) {
        visited.add(next);
        queue.push(next);
      }
    }
  }

  upstream.delete('start');
  upstream.delete('end');
  return upstream;
}

// ── Decision: available variables panel ───────────────────────────────────────

function VariableHelper({
  allNodes, edges, currentNodeId, onInsert, approvedActions,
}: {
  allNodes: Node<RunbookStepData>[];
  edges: Edge[];
  currentNodeId: string;
  onInsert: (ref: string) => void;
  approvedActions: ApprovedAction[];
}) {
  // Only show nodes that are strictly upstream (reachable from start before the decision)
  const upstreamIds = getUpstreamIds(currentNodeId, edges);
  const upstreamNodes = allNodes.filter(n =>
    upstreamIds.has(n.id) &&
    !['start', 'end', 'decision'].includes(n.data.stepType)
  );

  if (upstreamNodes.length === 0) {
    return (
      <div style={{ fontSize: 10, color: '#334155', padding: '8px 0', lineHeight: 1.6 }}>
        Connect diagnostic or action steps upstream of this step to see available variables.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {upstreamNodes.map(n => {
        const tool = n.data.tool || '';
        const allVars = getAutoOutputs(tool, approvedActions);

        if (allVars.length === 0 && !tool) return null;

        return (
          <div key={n.id} style={{ background: '#13161f', border: '1px solid #1e2231', borderRadius: 6, padding: '8px 10px' }}>
            <div style={{ fontSize: 10, color: '#475569', fontFamily: 'monospace', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ color: '#7c85a0', fontWeight: 600 }}>{n.data.name || n.id}</span>
              {tool && <span style={{ color: '#334155' }}>· {tool}</span>}
            </div>
            {allVars.length === 0 ? (
              <div style={{ fontSize: 10, color: '#334155' }}>No known outputs for this tool</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {allVars.map(v => {
                  const varRef = `${n.id}.${v.name}`;
                  return (
                    <div key={v.name} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <button
                        draggable
                        onDragStart={e => {
                          e.dataTransfer.setData('text/plain', varRef);
                          e.dataTransfer.effectAllowed = 'copy';
                        }}
                        onClick={() => onInsert(varRef)}
                        title={`Click or drag to insert ${varRef}`}
                        style={{ fontFamily: 'monospace', fontSize: 10, color: '#60a5fa', background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.25)', borderRadius: 4, padding: '2px 6px', cursor: 'grab', flexShrink: 0, whiteSpace: 'nowrap', userSelect: 'none' }}>
                        ⠿ {varRef}
                      </button>
                      <span style={{ fontSize: 9, color: '#334155', flexShrink: 0, background: '#1e2231', borderRadius: 3, padding: '1px 4px', fontFamily: 'monospace' }}>{v.type}</span>
                      <span style={{ fontSize: 9, color: '#4a5068', lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.desc}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      {/* Condition syntax hint */}
      <div style={{ fontSize: 9, color: '#334155', lineHeight: 1.7, borderTop: '1px solid #1e2231', paddingTop: 6 }}>
        <span style={{ color: '#475569', fontWeight: 600 }}>Syntax: </span>
        <code style={{ color: '#60a5fa' }}>step_id.variable</code>
        <span style={{ color: '#334155' }}> operator </span>
        <code style={{ color: '#7c85a0' }}>value</code>
        <br />
        <span style={{ color: '#334155' }}>Operators: </span>
        <code style={{ color: '#7c85a0' }}>{'== != > >= < <='}</code>
        <br />
        <span style={{ color: '#475569', fontWeight: 600 }}>Examples:</span><br />
        <code style={{ color: '#94a3b8' }}>diag_1.error_count == 0</code><br />
        <code style={{ color: '#94a3b8' }}>diag_1.disk_percent &gt; 80</code><br />
        <code style={{ color: '#94a3b8' }}>diag_1.has_errors</code>
      </div>
    </div>
  );
}

// ── Verification: metric picker ───────────────────────────────────────────────

function MetricHelper({
  tool,
  onInsert,
  approvedActions,
}: {
  tool: string;
  onInsert: (varName: string) => void;
  approvedActions: ApprovedAction[];
}) {
  const allVars = getAutoOutputs(tool, approvedActions);

  if (allVars.length === 0) return null;

  return (
    <div style={{ marginTop: 6, background: '#13161f', border: '1px solid #1e2231', borderRadius: 6, padding: '7px 9px' }}>
      <div style={{ fontSize: 9, color: '#475569', fontWeight: 700, marginBottom: 5, textTransform: 'uppercase', letterSpacing: '.08em' }}>
        Available metrics — click to insert
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
        {allVars.map(v => (
          <button
            key={v.name}
            onClick={() => onInsert(v.name)}
            title={`${v.type}: ${v.desc}`}
            style={{ fontFamily: 'monospace', fontSize: 10, color: '#a78bfa', background: 'rgba(167,139,250,0.1)', border: '1px solid rgba(167,139,250,0.25)', borderRadius: 4, padding: '2px 6px', cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none' }}>
            {v.name}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export function PropertiesPanel({ node, allNodeIds, allNodes, edges, onChange, onDelete, approvedActions }: Props) {
  const [newArgKey, setNewArgKey]         = useState('');
  const [newArgVal, setNewArgVal]         = useState('');
  const conditionRef = useRef<HTMLInputElement>(null);
  const runIfRef = useRef<HTMLInputElement>(null);
  const [focusedArgKey, setFocusedArgKey] = useState<string | null>(null);
  const argInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  if (!node) {
    return (
      <div style={panelStyle}>
        <div style={{ padding: 24, color: '#334155', fontSize: 12, textAlign: 'center', marginTop: 40, lineHeight: 1.6 }}>
          Click any node to edit its properties
        </div>
      </div>
    );
  }

  const d = node.data;
  const set = (patch: Partial<RunbookStepData>) => onChange(node.id, patch);

  const addArg = () => {
    if (!newArgKey) return;
    set({ args: { ...(d.args || {}), [newArgKey]: newArgVal } });
    setNewArgKey(''); setNewArgVal('');
  };
  const removeArg = (key: string) => {
    const a = { ...(d.args || {}) }; delete a[key]; set({ args: a });
  };

  const handleToolSelect = (action: ApprovedAction) => {
    const newArgs: Record<string, string> = {};
    for (const p of (Array.isArray(action.parameters) ? action.parameters : [])) {
      if (p.name === 'target') continue;
      newArgs[p.name] = d.args?.[p.name] ?? p.default ?? '';
    }
    // outputCapture is dead data from a removed feature (never evaluated by the
    // backend) — scrub it on tool change so old runbooks self-clean on edit.
    set({ tool: action.tool_name, args: newArgs, outputCapture: {} });
  };

  // Insert a variable reference into a condition-like text input at cursor position.
  // Shared by the Decision "condition" field and any step's "run_if" field — both use
  // the exact same expression syntax/evaluator, so they get the same insert behavior.
  const insertVarAt = (
    field: 'condition' | 'run_if',
    elRef: React.RefObject<HTMLInputElement | null>,
    ref: string,
  ) => {
    const el = elRef.current;
    const current = (d[field] as string) || '';
    if (!el) { set({ [field]: (current + (current ? ' ' : '') + ref).trim() } as Partial<RunbookStepData>); return; }
    const start = el.selectionStart ?? el.value.length;
    const end   = el.selectionEnd   ?? el.value.length;
    const before = el.value.slice(0, start);
    const after  = el.value.slice(end);
    const sep    = before && !before.endsWith(' ') ? ' ' : '';
    const newVal = before + sep + ref + after;
    set({ [field]: newVal } as Partial<RunbookStepData>);
    // Restore cursor after React re-render
    setTimeout(() => { el.focus(); el.setSelectionRange(start + sep.length + ref.length, start + sep.length + ref.length); }, 10);
  };
  const insertConditionVar = (ref: string) => insertVarAt('condition', conditionRef, ref);
  const insertRunIfVar     = (ref: string) => insertVarAt('run_if', runIfRef, ref);

  // Insert a variable reference into whichever arg field was last focused, wrapped in
  // {{...}} — args/message use the backend's {{step_id.field}} string-substitution
  // syntax (agentic_os.agents.incident_agents.ToolRegistryAgent._resolve_step_references),
  // not the bare step_id.field condition-language run_if/condition use, so unlike
  // insertVarAt above this always wraps the reference rather than inserting it bare.
  const insertArgVar = (ref: string) => {
    if (!focusedArgKey) return;
    const wrapped = `{{${ref}}}`;
    const el = argInputRefs.current[focusedArgKey];
    const current = String(d.args?.[focusedArgKey] ?? '');
    if (!el) {
      set({ args: { ...(d.args || {}), [focusedArgKey]: (current + (current ? ' ' : '') + wrapped) } });
      return;
    }
    const start = el.selectionStart ?? el.value.length;
    const end   = el.selectionEnd   ?? el.value.length;
    const before = el.value.slice(0, start);
    const after  = el.value.slice(end);
    const newVal = before + wrapped + after;
    set({ args: { ...(d.args || {}), [focusedArgKey]: newVal } });
    setTimeout(() => { el.focus(); el.setSelectionRange(start + wrapped.length, start + wrapped.length); }, 10);
  };

  const isDecision       = d.stepType === 'decision';
  const isVerification   = d.stepType === 'verification';
  const isTerminal       = d.stepType === 'start' || d.stepType === 'end';
  const isWait           = d.stepType === 'wait';
  const isAction         = d.stepType === 'action';
  const isIncidentUpdate = d.stepType === 'incident_update';

  return (
    <div style={panelStyle}>
      {/* Header */}
      <div style={{ padding: '13px 15px 11px', borderBottom: '1px solid #1e2a3a' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>{d.name || d.stepType}</div>
          {!isTerminal && (
            <button onClick={() => onDelete(node.id)} style={{ background: 'none', border: '1px solid #1e2a3a', borderRadius: 4, color: '#475569', cursor: 'pointer', fontSize: 10, padding: '2px 6px' }} title="Delete node">del</button>
          )}
        </div>
        <div style={{ fontSize: 10, color: '#7c85a0', marginTop: 4 }}>
          ID: <span style={{ fontFamily: 'monospace', color: '#4a5068' }}>{node.id}</span>
        </div>
      </div>

      <div style={{ padding: '13px 15px', flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>

        {/* ── Name ── */}
        {!isTerminal && !isDecision && (
          <Field label="Name">
            <input style={inputSt} value={d.name || ''} onChange={e => set({ name: e.target.value })} placeholder="Step name" />
          </Field>
        )}

        {/* ── Duration (wait steps only) ── */}
        {isWait && (
          <Field label="Duration (seconds)">
            <input
              style={inputSt}
              type="number"
              min={1}
              value={d.duration_seconds ?? ''}
              onChange={e => set({ duration_seconds: e.target.value ? Number(e.target.value) : undefined })}
              placeholder="e.g. 30"
            />
            <div style={{ fontSize: 10, color: '#4a5068', marginTop: 3 }}>Runbook pauses here for this many seconds before continuing</div>
          </Field>
        )}

        {/* ── Incident state (incident_update steps only) ── */}
        {isIncidentUpdate && (
          <Field label="Set Incident State">
            <select style={inputSt} value={d.state || 'resolved'} onChange={e => set({ state: e.target.value })}>
              <option value="resolved">Resolved</option>
              <option value="escalated">Escalated</option>
              <option value="acknowledged">Acknowledged</option>
            </select>
            <div style={{ fontSize: 10, color: '#4a5068', marginTop: 3 }}>
              Only runs if every step before it succeeded — verification failing aborts the
              runbook before reaching this step, so no condition is needed here.
            </div>
          </Field>
        )}

        {/* ── Tool ── */}
        {!isTerminal && !isDecision && !isWait && !isIncidentUpdate && (
          <Field label="Tool">
            <ToolCombobox value={d.tool || ''} onChange={v => set({ tool: v })} onSelect={handleToolSelect} approvedActions={approvedActions} />
          </Field>
        )}

        {/* ── Description ── */}
        {!isTerminal && !isDecision && (
          <Field label="Description">
            <textarea style={{ ...inputSt, resize: 'vertical', minHeight: 52 }} value={d.description || ''} onChange={e => set({ description: e.target.value })} placeholder="What does this step do?" />
          </Field>
        )}

        {/* ── Retry (action steps only) ── */}
        {isAction && (
          <Field label="Retry on failure">
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 9, color: '#475569', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '.06em' }}>Attempts</div>
                <input
                  style={inputSt}
                  type="number"
                  min={0}
                  max={10}
                  value={d.retry_count ?? ''}
                  onChange={e => set({ retry_count: e.target.value ? Number(e.target.value) : undefined })}
                  placeholder="0"
                />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 9, color: '#475569', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '.06em' }}>Delay (s)</div>
                <input
                  style={inputSt}
                  type="number"
                  min={0}
                  value={d.retry_delay_seconds ?? ''}
                  onChange={e => set({ retry_delay_seconds: e.target.value ? Number(e.target.value) : undefined })}
                  placeholder="5"
                />
              </div>
            </div>
            <div style={{ fontSize: 10, color: '#4a5068', marginTop: 3 }}>Re-run this step up to N times before marking failed</div>
          </Field>
        )}

        {/* ── Decision fields ── */}
        {isDecision && (
          <>
            <Field label="Condition">
              <input
                ref={conditionRef}
                style={{ ...inputSt, fontFamily: 'monospace' }}
                value={d.condition || ''}
                onChange={e => set({ condition: e.target.value })}
                onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; }}
                onDrop={e => {
                  e.preventDefault();
                  const text = e.dataTransfer.getData('text/plain');
                  if (text) insertConditionVar(text);
                }}
                placeholder="e.g. diag_1.error_count == 0 — or drag a variable pill here"
              />
              <div style={{ fontSize: 9, color: '#334155', marginTop: 3 }}>
                true → right handle &nbsp;·&nbsp; false → left handle
              </div>
              {validateCondition(d.condition || '', allNodes, approvedActions).map((w, i) => (
                <div key={i} style={{ fontSize: 9, color: '#f59e0b', marginTop: 3, display: 'flex', gap: 4, alignItems: 'flex-start' }}>
                  <span style={{ flexShrink: 0 }}>⚠</span><span>{w}</span>
                </div>
              ))}
            </Field>

            {/* Read-only routing — derived from graph edges, not editable data fields.
                The executor follows the actual drawn edges (sourceHandle true/false),
                so these are always in sync with the graph. */}
            {(['true', 'false'] as const).map(branch => {
              const edge       = edges.find(e => e.source === node.id && e.sourceHandle === branch);
              const targetNode = edge ? allNodes.find(n => n.id === edge.target) : undefined;
              const color      = branch === 'true' ? '#10b981' : '#e85d75';
              const bg         = branch === 'true' ? '#061a12' : '#1a0610';
              const handleDesc = branch === 'true' ? 'right (green ●)' : 'left (pink ●)';
              return (
                <Field key={branch} label={`On ${branch.toUpperCase()} — routes to`}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: edge ? bg : '#13161f', border: `1px solid ${edge ? color + '55' : '#1e2231'}`, borderRadius: 6, minHeight: 32 }}>
                    {edge ? (
                      <>
                        <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, flexShrink: 0, display: 'inline-block' }} />
                        <span style={{ fontFamily: 'monospace', fontSize: 11, color, fontWeight: 600 }}>
                          {targetNode?.data?.name || edge.target}
                        </span>
                        {targetNode?.data?.name && (
                          <span style={{ fontSize: 10, color: '#475569', fontFamily: 'monospace' }}>({edge.target})</span>
                        )}
                      </>
                    ) : (
                      <span style={{ fontSize: 10, color: '#334155', fontStyle: 'italic' }}>
                        Not connected — drag from the {handleDesc} handle
                      </span>
                    )}
                  </div>
                </Field>
              );
            })}

            {/* Variable reference helper */}
            <Field label="Available variables — click or drag to insert">
              <VariableHelper allNodes={allNodes} edges={edges} currentNodeId={node.id} onInsert={insertConditionVar} approvedActions={approvedActions} />
            </Field>
          </>
        )}

        {/* ── Verification fields ── */}
        {isVerification && (
          <>
            <Field label="Metric">
              <input style={{ ...inputSt, fontFamily: 'monospace' }} value={d.metric || ''} onChange={e => set({ metric: e.target.value })} placeholder="e.g. disk_percent" />
              <MetricHelper tool={d.tool || ''} onInsert={v => set({ metric: v })} approvedActions={approvedActions} />
            </Field>
            <Field label="Check">
              <select style={inputSt} value={d.check || ''} onChange={e => set({ check: e.target.value })}>
                <option value="">— select —</option>
                <option value="less_than">less than</option>
                <option value="greater_than">greater than</option>
                <option value="equals">equals</option>
                <option value="not_equals">not equals</option>
                <option value="contains">contains</option>
              </select>
            </Field>
            <Field label="Value">
              <input style={inputSt} value={d.value || ''} onChange={e => set({ value: e.target.value })} placeholder="e.g. 75" />
            </Field>
          </>
        )}

        {/* ── Args ── */}
        {!isTerminal && !isDecision && !isWait && (
          <Field label="Parameters (args)">
            {Object.entries(d.args || {}).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: 4, marginBottom: 4, alignItems: 'center' }}>
                <input style={{ ...inputSt, flex: 1, fontFamily: 'monospace', fontSize: 10.5 }} value={k} readOnly />
                <span style={{ color: '#4a5068' }}>→</span>
                {k === 'team' ? (
                  <NotificationTeamArgCombobox
                    value={String(v)}
                    onChange={nv => set({ args: { ...(d.args || {}), [k]: nv } })}
                  />
                ) : (
                  <input
                    ref={el => { argInputRefs.current[k] = el; }}
                    style={{ ...inputSt, flex: 1, fontFamily: 'monospace', fontSize: 10.5 }}
                    value={v}
                    onChange={e => set({ args: { ...(d.args || {}), [k]: e.target.value } })}
                    onFocus={() => setFocusedArgKey(k)}
                    onDragOver={e => e.preventDefault()}
                    onDrop={e => {
                      e.preventDefault();
                      const ref = e.dataTransfer.getData('text/plain');
                      if (!ref) return;
                      setFocusedArgKey(k);
                      argInputRefs.current[k] = e.currentTarget;
                      insertArgVar(ref);
                    }}
                    placeholder="value — or drag a variable pill here"
                  />
                )}
                <button onClick={() => removeArg(k)} style={iconBtn}>✕</button>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
              <input style={{ ...inputSt, flex: 1, fontFamily: 'monospace', fontSize: 10.5 }} value={newArgKey} onChange={e => setNewArgKey(e.target.value)} placeholder="key" />
              <input style={{ ...inputSt, flex: 1, fontFamily: 'monospace', fontSize: 10.5 }} value={newArgVal} onChange={e => setNewArgVal(e.target.value)} placeholder="value" onKeyDown={e => e.key === 'Enter' && addArg()} />
              <button onClick={addArg} style={{ ...iconBtn, color: '#10b981' }}>+</button>
            </div>
            {Object.keys(d.args || {}).length > 0 && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 9, color: '#4a5068', marginBottom: 4 }}>
                  {focusedArgKey
                    ? <>Inserting into <code style={{ color: '#60a5fa' }}>{focusedArgKey}</code> — click a field above to switch, or drag a pill directly onto any arg.</>
                    : 'Click an arg value above, then click a variable below to insert it there — or drag a pill directly onto any arg.'}
                </div>
                <VariableHelper allNodes={allNodes} edges={edges} currentNodeId={node.id} onInsert={insertArgVar} approvedActions={approvedActions} />
              </div>
            )}
          </Field>
        )}

        {/* ── Output (informational — fields come from the tool's own catalog
               definition; add new ones in Approved Actions → Output Fields,
               not here) ── */}
        {(d.stepType === 'diagnostic' || d.stepType === 'action' || d.stepType === 'verification') &&
          d.tool && getAutoOutputs(d.tool, approvedActions).length > 0 && (
          <Field label="Output">
            <div style={{ fontSize: 9, color: '#334155', background: '#0d0f14', border: '1px solid #1e2231', borderRadius: 4, padding: '5px 8px', lineHeight: 1.6 }}>
              <span style={{ color: '#475569', fontWeight: 700 }}>Auto-parsed: </span>
              {getAutoOutputs(d.tool, approvedActions).map(v => (
                <span key={v.name} style={{ color: '#60a5fa', fontFamily: 'monospace' }}>{v.name}</span>
              )).reduce((acc: any[], el, i, arr) => [...acc, el, i < arr.length - 1 ? <span key={`s${i}`} style={{ color: '#334155' }}>, </span> : null], [])}
              <span style={{ color: '#334155' }}> — usable in decisions/run_if as {`{step_id}.{field}`}</span>
            </div>
          </Field>
        )}

        {/* ── run_if ── */}
        {!isTerminal && !isDecision && (
          <Field label="run_if (optional)">
            <input
              ref={runIfRef}
              style={{ ...inputSt, fontFamily: 'monospace', fontSize: 10.5 }}
              value={d.run_if || ''}
              onChange={e => set({ run_if: e.target.value })}
              onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; }}
              onDrop={e => {
                e.preventDefault();
                const text = e.dataTransfer.getData('text/plain');
                if (text) insertRunIfVar(text);
              }}
              placeholder="e.g. step_1.disk_percent <= 90 — or drag a variable pill here"
            />
            <div style={{ fontSize: 10, color: '#4a5068', marginTop: 3 }}>Step is skipped if condition is false</div>
            {validateCondition(d.run_if || '', allNodes, approvedActions).map((w, i) => (
              <div key={i} style={{ fontSize: 9, color: '#f59e0b', marginTop: 3, display: 'flex', gap: 4, alignItems: 'flex-start' }}>
                <span style={{ flexShrink: 0 }}>⚠</span><span>{w}</span>
              </div>
            ))}
            <div style={{ marginTop: 6 }}>
              <VariableHelper allNodes={allNodes} edges={edges} currentNodeId={node.id} onInsert={insertRunIfVar} approvedActions={approvedActions} />
            </div>
          </Field>
        )}

        {/* ── on_failure ── */}
        {(d.stepType === 'action' || d.stepType === 'diagnostic' || d.stepType === 'notify') && (
          <Field label="On Failure">
            <select
              style={inputSt}
              value={d.on_failure || 'abort'}
              onChange={e => set({ on_failure: e.target.value as 'abort' | 'continue' })}
            >
              <option value="abort">Halt runbook (default)</option>
              <option value="continue">Continue to next step</option>
            </select>
            <div style={{ fontSize: 10, color: d.on_failure === 'continue' ? '#f59e0b' : '#4a5068', marginTop: 3 }}>
              {d.on_failure === 'continue'
                ? '⚠ Runbook continues even if this step fails'
                : 'Runbook stops here if this step fails — recommended for critical steps'}
            </div>
          </Field>
        )}

      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 9, fontWeight: 700, color: '#334155', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 5 }}>{label}</div>
      {children}
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  width: 340, background: '#0d1018', borderLeft: '1px solid #1e2a3a',
  display: 'flex', flexDirection: 'column', flexShrink: 0, overflow: 'hidden',
};

const inputSt: React.CSSProperties = {
  width: '100%', background: '#161b28', border: '1px solid #2a3548',
  borderRadius: 5, padding: '6px 8px', fontSize: 12, color: '#e2e8f0',
  outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box',
};

const iconBtn: React.CSSProperties = {
  background: 'none', border: '1px solid #1e2a3a', borderRadius: 4,
  color: '#475569', cursor: 'pointer', padding: '3px 6px', fontSize: 11, flexShrink: 0,
};
