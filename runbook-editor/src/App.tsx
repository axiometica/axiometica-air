import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap, addEdge,
  useNodesState, useEdgesState, type Node, type Edge,
  type Connection, BackgroundVariant, Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { StepNode } from './nodes/StepNode';
import { Sidebar, EventTypeCombobox } from './components/Sidebar';
import { PropertiesPanel } from './components/PropertiesPanel';
import { JsonPanel } from './components/JsonPanel';
import type { RunbookStepData, StepType, RunbookJSON, RunbookStep } from './types';

const API_BASE = '/api';

const nodeTypes = { step: StepNode };

let nodeIdCounter = 10;
const newId = (type: StepType) => `${type}_${++nodeIdCounter}`;

// ── Approved action type ──────────────────────────────────────────────────────

export interface OutputField {
  field: string;
  kind: 'regex' | 'jsonpath';
  pattern: string;
  type: 'boolean' | 'integer' | 'float' | 'string';
}

export interface ApprovedAction {
  id?: string;
  tool_name: string;
  name: string;
  category: string;
  description: string;
  parameters: Array<{
    name: string;
    type: string;
    required: boolean;
    default?: string;
    description?: string;
  }>;
  enabled: boolean;
  blast_radius?: number;
  output_fields?: OutputField[];
  is_builtin?: boolean;
}

// ── Blank canvas (no demo) ────────────────────────────────────────────────────

const BLANK_NODES: Node<RunbookStepData>[] = [
  { id: 'start', type: 'step', position: { x: 300, y: 20  }, data: { id: 'start', stepType: 'start', name: 'START' }, deletable: false },
  { id: 'end',   type: 'step', position: { x: 300, y: 200 }, data: { id: 'end',   stepType: 'end',   name: 'END'   }, deletable: false },
];
const BLANK_EDGES: Edge[] = [];

// ── JSON builder ──────────────────────────────────────────────────────────────

interface RunbookMeta {
  description: string;
  platform: string;
  service: string;
  blast_radius: number;
  enabled: boolean;
}

const DEFAULT_META: RunbookMeta = { description: '', platform: 'any', service: '', blast_radius: 2, enabled: true };

function buildJson(
  name: string,
  trigger: string,
  nodes: Node<RunbookStepData>[],
  _edges: Edge[],
  meta: RunbookMeta = DEFAULT_META,
  confidence = 0.80,
): RunbookJSON {
  const steps: RunbookStep[] = nodes
    .filter(n => n.data.stepType !== 'start' && n.data.stepType !== 'end')
    .map(n => {
      const d = n.data;
      const s: RunbookStep = { id: d.id, type: d.stepType };
      if (d.name)                                                        s.name = d.name;
      if (d.tool)                                                        s.tool = d.tool;
      if (d.description)                                                 s.description = d.description;
      if (d.args && Object.keys(d.args).length)                         s.args = d.args;
      if (d.outputCapture && Object.keys(d.outputCapture).length)       s.output_capture = d.outputCapture;
      if (d.run_if)                                                      s.run_if = d.run_if;
      if (d.on_failure)                                                  s.on_failure = d.on_failure;
      if (d.metric)                                                      s.metric = d.metric;
      if (d.check)                                                       s.check = d.check;
      if (d.value)                                                       s.value = d.value;
      if (d.condition)                                                   s.condition = d.condition;
      // on_true / on_false must be serialised so parseRunbookJSON can reconstruct
      // decision-branch edges when loading a saved runbook (enabling graph-walk execution).
      if (d.on_true)                                                     s.on_true = d.on_true;
      if (d.on_false)                                                    s.on_false = d.on_false;
      if (d.duration_seconds != null && d.duration_seconds > 0)         s.duration_seconds = d.duration_seconds;
      if (d.retry_count != null && d.retry_count > 0)                   s.retry_count = d.retry_count;
      if (d.retry_delay_seconds != null && d.retry_delay_seconds > 0)   s.retry_delay_seconds = d.retry_delay_seconds;
      return s;
    });
  return {
    name,
    trigger_type: trigger,
    description:  meta.description,
    platform:     meta.platform,
    service:      meta.service || undefined,
    confidence,
    blast_radius: meta.blast_radius,
    enabled:      meta.enabled,
    steps,
  };
}

// ── DB → editor steps (for runbooks without source_steps) ─────────────────────

function dbRunbookToEditorSteps(rb: Record<string, any>): RunbookStep[] {
  const steps: RunbookStep[] = [];

  (rb.diagnostics || []).forEach((d: any, i: number) => {
    steps.push({
      id: d.id || `diag_${i + 1}`,
      type: 'diagnostic',
      name: d.name || d.description || `Diagnostic ${i + 1}`,
      tool: d.tool || '',
      description: d.description || '',
      args: d.args_json || d.args || {},
      output_capture: d.output_capture || {},
    });
  });

  (rb.actions || []).forEach((a: any, i: number) => {
    steps.push({
      id: a.id || `action_${i + 1}`,
      type: 'action',
      name: a.name || a.description || `Action ${i + 1}`,
      tool: a.tool || '',
      description: a.description || '',
      args: a.args_json || a.args || {},
      run_if: a.run_if || a.condition || '',
    });
  });

  (rb.verification_steps || []).forEach((v: any, i: number) => {
    steps.push({
      id: v.id || `verify_${i + 1}`,
      type: 'verification',
      name: v.name || v.description || `Verify ${i + 1}`,
      description: v.description || '',
      metric: v.metric || '',
      check: v.threshold_type || v.check || '',
      value: String(v.threshold ?? v.value ?? ''),
    });
  });

  return steps;
}

// ── Longest-path layout — correct positions for branching/converging graphs ───
//
// Uses Kahn's topological sort to compute the longest path from START to each
// node. This is strictly correct for DAGs and handles:
//   • Convergence nodes (multiple paths merging) — depth = max incoming path
//   • Early exits to END — END always appears at the very bottom
//   • Cascading updates — no BFS "else-if" hack needed; Kahn processes each
//     node only after all its predecessors have been finalised

function computeBFSLayout(
  nodes: Node<RunbookStepData>[],
  edges: Edge[],
): Node<RunbookStepData>[] {
  // ── Phase 1: Longest-path depth via Kahn's topological sort ──────────────
  const adj      = new Map<string, string[]>();
  const indegree = new Map<string, number>();
  nodes.forEach(n => { adj.set(n.id, []); indegree.set(n.id, 0); });
  edges.forEach(e => {
    if (!adj.get(e.source)?.includes(e.target)) {
      adj.get(e.source)?.push(e.target);
      indegree.set(e.target, (indegree.get(e.target) ?? 0) + 1);
    }
  });

  const depthMap = new Map<string, number>();
  nodes.forEach(n => depthMap.set(n.id, 0));

  // Seed: all nodes with no incoming edges (typically just 'start')
  const topoQueue = nodes.map(n => n.id).filter(id => (indegree.get(id) ?? 0) === 0);
  while (topoQueue.length > 0) {
    const curr     = topoQueue.shift()!;
    const currDepth = depthMap.get(curr) ?? 0;
    for (const next of adj.get(curr) ?? []) {
      // Longest path: push depth to max(current, parent+1)
      depthMap.set(next, Math.max(depthMap.get(next) ?? 0, currDepth + 1));
      const newDeg = (indegree.get(next) ?? 1) - 1;
      indegree.set(next, newDeg);
      if (newDeg === 0) topoQueue.push(next);   // all predecessors done → enqueue
    }
  }

  // END must always sit below every other node regardless of graph shape
  const maxOtherDepth = Math.max(
    ...[...depthMap.entries()].filter(([id]) => id !== 'end').map(([, d]) => d),
    0,
  );
  depthMap.set('end', maxOtherDepth + 1);

  // ── Phase 2: Column assignment via parent-edge tracking ──────────────────
  const parentEdges = new Map<string, { source: string; sh: string | undefined }[]>();
  nodes.forEach(n => parentEdges.set(n.id, []));
  edges.forEach(e => {
    parentEdges.get(e.target)?.push({ source: e.source, sh: e.sourceHandle ?? undefined });
  });

  const colMap = new Map<string, number>();
  colMap.set('start', 0);

  // Process in ascending depth order — guaranteed to have parent cols ready
  const sorted = [...nodes].sort(
    (a, b) => (depthMap.get(a.id) ?? 0) - (depthMap.get(b.id) ?? 0),
  );

  for (const node of sorted) {
    if (node.id === 'start') continue;
    const parents = parentEdges.get(node.id) ?? [];
    if (parents.length === 0) {
      colMap.set(node.id, 0);
    } else if (parents.length > 1) {
      // Convergence: inherit column from the deepest (most recent) parent
      const deepest = parents.reduce((best, p) =>
        (depthMap.get(p.source) ?? 0) >= (depthMap.get(best.source) ?? 0) ? p : best,
      );
      const parentCol = colMap.get(deepest.source) ?? 0;
      const delta = deepest.sh === 'true' ? 1 : deepest.sh === 'false' ? -1 : 0;
      colMap.set(node.id, parentCol + delta);
    } else {
      const { source, sh } = parents[0];
      const parentCol = colMap.get(source) ?? 0;
      const delta = sh === 'true' ? 1 : sh === 'false' ? -1 : 0;
      colMap.set(node.id, parentCol + delta);
    }
  }

  const ROW = 200;
  const COL = 310;
  const CX  = 500;

  return nodes.map(n => ({
    ...n,
    position: {
      x: CX + (colMap.get(n.id) ?? 0) * COL,
      y: (depthMap.get(n.id) ?? 0) * ROW + 20,
    },
  }));
}

// ── JSON importer ─────────────────────────────────────────────────────────────

type ImportResult = { nodes: Node<RunbookStepData>[]; edges: Edge[]; name: string; trigger: string } | string;

function parseRunbookJSON(text: string): ImportResult {
  let json: RunbookJSON;
  try { json = JSON.parse(text); } catch { return 'Invalid JSON — check for syntax errors.'; }
  if (!json.steps || !Array.isArray(json.steps)) return 'JSON must have a "steps" array.';

  const stepIds = new Set(json.steps.map(s => s.id));

  const newNodes: Node<RunbookStepData>[] = [
    { id: 'start', type: 'step', position: { x: 350, y: 20 }, data: { id: 'start', stepType: 'start', name: 'START' }, deletable: false },
  ];

  const ROW = 180;
  json.steps.forEach((step, i) => {
    newNodes.push({
      id: step.id, type: 'step',
      position: { x: 350, y: (i + 1) * ROW },
      data: {
        id: step.id, stepType: step.type,
        name: step.name || '',
        tool: step.tool || '',
        description: step.description || '',
        args: step.args || {},
        outputCapture: step.output_capture || {},
        run_if: step.run_if || '',
        on_failure: step.on_failure || 'abort',
        metric: step.metric || '',
        check: step.check || '',
        value: step.value || '',
        condition: step.condition || '',
        on_true: step.on_true || '',
        on_false: step.on_false || '',
        duration_seconds: step.duration_seconds || undefined,
        retry_count: step.retry_count || undefined,
        retry_delay_seconds: step.retry_delay_seconds || undefined,
      },
    });
  });

  newNodes.push({
    id: 'end', type: 'step', position: { x: 350, y: (json.steps.length + 1) * ROW },
    data: { id: 'end', stepType: 'end', name: 'END' }, deletable: false,
  });

  const newEdges: Edge[] = [];
  const seen = new Set<string>();

  // Steps that are explicit decision-branch targets get their real parent from
  // that branch edge, not from array order — the implicit "next in array" edge
  // is suppressed for them below. Without this, a branch target ends up with
  // two parents (the real branch edge + a bogus array-adjacency edge), which
  // computeBFSLayout treats as convergence and force-centres to column 0 —
  // collapsing the whole graph into a single column despite real branching.
  const decisionTargets = new Set<string>();
  json.steps.forEach(step => {
    if (step.type === 'decision') {
      if (step.on_true  && stepIds.has(step.on_true))  decisionTargets.add(step.on_true);
      if (step.on_false && stepIds.has(step.on_false)) decisionTargets.add(step.on_false);
    }
  });

  json.steps.forEach((step, i) => {
    const prevStep = i === 0 ? null : json.steps[i - 1];
    const prevId   = i === 0 ? 'start' : prevStep!.id;
    // Suppress the implicit array-order edge when the previous step is a decision
    // (its outgoing flow is fully and explicitly defined by on_true/on_false — there
    // is no fallthrough) or when this step is itself a declared branch target.
    const skipImplicit = prevStep?.type === 'decision' || decisionTargets.has(step.id);
    if (!skipImplicit) {
      const eid = `e-${prevId}-${step.id}`;
      if (!seen.has(eid)) {
        seen.add(eid);
        newEdges.push({ id: eid, source: prevId, target: step.id, style: { stroke: '#2d3450', strokeWidth: 2 } });
      }
    }
    if (step.type === 'decision') {
      if (step.on_true && stepIds.has(step.on_true)) {
        const id = `e-${step.id}-true`;
        if (!seen.has(id)) { seen.add(id); newEdges.push({ id, source: step.id, target: step.on_true, sourceHandle: 'true', style: { stroke: '#10b981', strokeWidth: 2 }, label: 'true', labelStyle: { fill: '#10b981', fontSize: 10 }, labelBgStyle: { fill: '#0d3025' } }); }
      }
      if (step.on_false && stepIds.has(step.on_false)) {
        const id = `e-${step.id}-false`;
        if (!seen.has(id)) { seen.add(id); newEdges.push({ id, source: step.id, target: step.on_false, sourceHandle: 'false', style: { stroke: '#f43f5e', strokeWidth: 2, strokeDasharray: '5 3' }, label: 'false', labelStyle: { fill: '#f43f5e', fontSize: 10 }, labelBgStyle: { fill: '#3d0a14' } }); }
      }
    }
  });
  if (json.steps.length) {
    const lastStep = json.steps[json.steps.length - 1];
    if (lastStep.type !== 'decision') {
      const eid = `e-${lastStep.id}-end`;
      if (!seen.has(eid)) newEdges.push({ id: eid, source: lastStep.id, target: 'end', style: { stroke: '#10b981', strokeWidth: 2 } });
    }
  }

  const layoutedNodes = computeBFSLayout(newNodes as Node<RunbookStepData>[], newEdges);
  return { nodes: layoutedNodes, edges: newEdges, name: json.name || 'Imported Runbook', trigger: json.trigger_type || '' };
}

// ── Live execution types ──────────────────────────────────────────────────────

interface StepResult {
  step: number; node_id?: string; name: string; tool: string; step_type: string;
  skipped: boolean; success: boolean; raw_output: string; truncated?: boolean;
  structured: Record<string, unknown>; message: string; error: string;
  command: string; elapsed_ms: number; skip_reason?: string; dry_run?: boolean;
}
interface LiveResult {
  success: boolean; target: string; watcher: string; adapter: string;
  succeeded: number; skipped: number; failed: number; elapsed_ms: number;
  results: StepResult[];
}

// ── Simulation ────────────────────────────────────────────────────────────────

interface SimLog {
  id: string; name: string; type: string;
  status: 'success' | 'skipped' | 'failed';
  message: string; outputs?: Record<string, string>;
}

function runSimulation(
  nodes: Node<RunbookStepData>[], _edges: Edge[],
  setNodes: (u: any) => void, setEdges: (u: any) => void,
  diskPct: number, onLog: (l: SimLog) => void, onComplete: () => void,
) {
  const steps = nodes.filter(n => !['start', 'end'].includes(n.data.stepType));
  setNodes((ns: Node<RunbookStepData>[]) => ns.map(n => ({ ...n, data: { ...n.data, status: ['start','end'].includes(n.data.stepType) ? undefined : 'pending' as const, liveOutput: {} } })));
  setEdges((es: Edge[]) => es.map(e => ({ ...e, animated: false, style: { ...e.style, opacity: 0.2, strokeWidth: 1.5 } })));
  let delay = 0;
  steps.forEach((node, idx) => {
    delay += 550;
    setTimeout(() => {
      setNodes((ns: Node<RunbookStepData>[]) => ns.map(n => n.id !== node.id ? n : { ...n, data: { ...n.data, status: 'running' as const } }));
    }, delay);
    delay += 750;
    setTimeout(() => {
      const d = node.data;
      let status: SimLog['status'] = 'success';
      let liveOutput: Record<string, string> = {};
      let logMsg = 'Completed successfully';
      if (d.outputCapture) {
        liveOutput = Object.fromEntries(Object.keys(d.outputCapture).map(k => [k, k === 'disk_percent' ? String(diskPct) : '13.2']));
        logMsg = `Outputs captured: ${Object.entries(liveOutput).map(([k, v]) => `${k}=${v}`).join(', ')}`;
      }
      if (d.run_if) {
        if (d.run_if.includes('> 90') && diskPct <= 90)  { status = 'skipped'; logMsg = `Skipped — condition false: ${d.run_if} (disk=${diskPct})`; }
        if (d.run_if.includes('<= 90') && diskPct > 90)  { status = 'skipped'; logMsg = `Skipped — condition false: ${d.run_if} (disk=${diskPct})`; }
      }
      if (d.stepType === 'decision') {
        const result = diskPct > 90;
        status = 'success';
        liveOutput = { result: `${diskPct} > 90 → ${result ? 'TRUE' : 'FALSE'}` };
        logMsg = `Evaluated: ${diskPct} > 90 = ${result}  →  routing to "${result ? d.on_true : d.on_false}"`;
        const activeHandle = result ? 'true' : 'false';
        setEdges((es: Edge[]) => es.map(e => {
          if (e.source !== node.id) return e;
          const isActive = e.sourceHandle === activeHandle;
          return { ...e, animated: isActive, style: { ...e.style, opacity: isActive ? 1 : 0.1, strokeWidth: isActive ? 3 : 1 } };
        }));
      } else if (status === 'success') {
        setEdges((es: Edge[]) => es.map(e => e.target !== node.id ? e : { ...e, animated: true, style: { ...e.style, opacity: 1, strokeWidth: 2.5 } }));
      }
      if (d.stepType === 'verification') logMsg = `Checked ${d.metric} ${d.check} ${d.value} — PASSED`;
      setNodes((ns: Node<RunbookStepData>[]) => ns.map(n => n.id !== node.id ? n : { ...n, data: { ...n.data, status, liveOutput } }));
      onLog({ id: d.id, name: d.name || d.stepType, type: d.stepType, status, message: logMsg, outputs: liveOutput });
      if (idx === steps.length - 1) setTimeout(onComplete, 400);
    }, delay);
  });
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [nodes, setNodes, onNodesChange] = useNodesState(BLANK_NODES as any);
  const [edges, setEdges, onEdgesChange] = useEdgesState(BLANK_EDGES);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [view,           setView]           = useState<'graph' | 'json'>('graph');
  const [runbookName,    setRunbookName]     = useState('');
  const [triggerType,    setTriggerType]     = useState('');
  const [description,    setDescription]     = useState('');
  const [platform,       setPlatform]        = useState('any');
  const [service,        setService]         = useState('');
  const [confidence,     setConfidence]      = useState(0.80);
  const [blastRadius,    setBlastRadius]     = useState(2);
  const [rbEnabled,      setRbEnabled]       = useState(true);
  const [rbSource,       setRbSource]        = useState<string>('');
  const [generationPrompt, setGenerationPrompt] = useState<string>('');
  const [simDisk,        setSimDisk]         = useState('87');
  const [showSimBar,     setShowSimBar]      = useState(false);
  const [simLogs,        setSimLogs]         = useState<SimLog[]>([]);
  const [simRunning,     setSimRunning]      = useState(false);
  const [simDone,        setSimDone]         = useState(false);
  const [showGenerate,   setShowGenerate]    = useState(false);
  const [genDesc,        setGenDesc]         = useState('');
  const [genLoading,     setGenLoading]      = useState(false);
  const [genError,       setGenError]        = useState('');
  // Auth token — same origin as main app so localStorage is shared directly
  const [authToken] = useState<string>(() => localStorage.getItem('ap_token') || '');
  // Live execute
  const [showLiveBar,    setShowLiveBar]     = useState(false);
  const [liveTarget,     setLiveTarget]      = useState('');
  const [liveWatcher,    setLiveWatcher]     = useState('');
  const [liveRunning,    setLiveRunning]     = useState(false);
  const [liveResults,    setLiveResults]     = useState<LiveResult | null>(null);
  const [liveError,      setLiveError]       = useState('');
  const [onStepFailure,  setOnStepFailure]   = useState<'continue' | 'stop_actions' | 'stop_all'>('continue');
  // Incident context for test run — substituted into step args (service_url, process_name, etc.)
  const [ctxServiceUrl,  setCtxServiceUrl]   = useState('');
  const [ctxProcessName, setCtxProcessName]  = useState('');
  const [ctxAnomalyProc, setCtxAnomalyProc] = useState('');
  const [showCtxFields,  setShowCtxFields]   = useState(false);
  const [dryRun,         setDryRun]          = useState(false);
  // Registered watchers for dropdown
  const [watchers,       setWatchers]        = useState<Array<{ watcher_name: string; display_name: string; sentinel_container: string; status: string }>>([]);
  // Save status
  const [saveStatus,     setSaveStatus]      = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  // Human-readable save error — persists independently of saveStatus's 2.5s auto-reset
  // so the actual cause stays visible until the user dismisses it or saves successfully.
  const [saveError,      setSaveError]       = useState('');
  // Draft/publish workflow — Save writes a draft only; Publish promotes it to live.
  const [rbStatus,       setRbStatus]        = useState<'draft' | 'published'>('draft');
  const [hasUnpublishedChanges, setHasUnpublishedChanges] = useState(false);
  const [publishStatus,  setPublishStatus]   = useState<'idle' | 'publishing' | 'published' | 'error'>('idle');
  // Non-blocking lint from the publish endpoint — e.g. a path with no validation
  // step. Shown as a dismissible notice; publish already succeeded regardless.
  const [publishWarnings, setPublishWarnings] = useState<string[]>([]);
  // Edit mode — set when loaded from ?id= or after first POST
  const [editingId,      setEditingId]       = useState<string | null>(null);
  // Approved actions from backend
  const [approvedActions, setApprovedActions] = useState<ApprovedAction[]>([]);
  // Loading state when fetching a runbook by ID
  const [loadingRunbook, setLoadingRunbook]  = useState(false);
  // Signals the fitView effect to fire once rfInstance + nodes are both ready
  const [pendingFitView, setPendingFitView]  = useState(false);
  // Progressive reveal — displayedResults grows as each step settles;
  // liveRunningStep holds the step currently animating (null when idle/settled)
  const [displayedResults, setDisplayedResults] = useState<StepResult[]>([]);
  const [liveRunningStep, setLiveRunningStep]   = useState<StepResult | null>(null);

  const wrapRef     = useRef<HTMLDivElement>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const [rfInstance, setRfInstance] = useState<any>(null);

  // ── Undo / Redo history ───────────────────────────────────────────────────
  const historyRef = useRef<Array<{ nodes: any; edges: Edge[] }>>([]);
  const futureRef  = useRef<Array<{ nodes: any; edges: Edge[] }>>([]);
  // pushSnapshotRef is updated every render so callbacks always close over
  // the latest nodes/edges without stale-closure issues.
  const pushSnapshotRef = useRef<() => void>(() => {});
  pushSnapshotRef.current = () => {
    historyRef.current = [...historyRef.current.slice(-49), { nodes, edges }];
    futureRef.current = [];
  };

  // ── Validation ────────────────────────────────────────────────────────────
  const [validationErrors, setValidationErrors] = useState<Map<string, string>>(new Map());
  const [showValidation, setShowValidation]     = useState(false);

  const selectedNode = nodes.find(n => n.id === selectedNodeId) as Node<RunbookStepData> | undefined;
  const rbMeta: RunbookMeta = { description, platform, service, blast_radius: blastRadius, enabled: rbEnabled };
  const runbook = buildJson(runbookName, triggerType, nodes as Node<RunbookStepData>[], edges, rbMeta, confidence);

  // ── Load approved actions + runbook by ?id= on mount ─────────────────────

  useEffect(() => {
    // Fetch approved actions for tool picker
    const hdrs: Record<string, string> = {};
    const tok = localStorage.getItem('ap_token') || '';
    if (tok) hdrs['Authorization'] = `Bearer ${tok}`;

    fetch(`${API_BASE}/approved-actions`, { headers: hdrs })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((data: ApprovedAction[]) => setApprovedActions(data))
      .catch(() => {/* ignore */});

    // Fetch registered watchers for the live-execute dropdown
    fetch(`${API_BASE}/monitoring/watchers`, { headers: hdrs })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((data: any[]) => {
        const active = data.filter(w => w.registration_status === 'approved');
        setWatchers(active);
        if (active.length > 0) {
          setLiveWatcher(active[0].watcher_name);
          setLiveTarget(active[0].sentinel_container || '');
        }
      })
      .catch(() => {/* ignore */});

    // Load runbook if ?id= is present
    const params = new URLSearchParams(window.location.search);
    const id = params.get('id');
    if (!id) return;

    setLoadingRunbook(true);
    fetch(`${API_BASE}/runbooks/${id}`, { headers: hdrs })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((rb: Record<string, any>) => {
        setEditingId(id);
        setRunbookName(rb.name || '');
        setTriggerType(rb.event_type || rb.trigger_type || '');
        setDescription(rb.description || '');
        setPlatform(rb.platform || 'any');
        setService(rb.service || '');
        setConfidence(typeof rb.confidence === 'number' ? rb.confidence : 0.80);
        setBlastRadius(typeof rb.blast_radius === 'number' ? rb.blast_radius : 2);
        setRbEnabled(rb.enabled !== false);
        setRbSource(rb.source || '');
        setGenerationPrompt(rb.generation_prompt || '');
        setRbStatus(rb.status === 'published' ? 'published' : 'draft');
        setHasUnpublishedChanges(!!rb.has_unpublished_changes);

        // Prefer source_steps (preserves decisions/run_if) over 3-array reconstruction.
        // source_steps may be:
        //   - array  (legacy) : just the steps
        //   - object (new)    : { steps: [...], edges: [...] }
        let stepsJson: RunbookStep[];
        let savedEdges:     Edge[]                                  | null = null;
        let savedPositions: Record<string, { x: number; y: number }> | null = null;

        if (rb.source_steps) {
          const raw = typeof rb.source_steps === 'string'
            ? JSON.parse(rb.source_steps)
            : rb.source_steps;
          if (Array.isArray(raw)) {
            stepsJson = raw;                              // legacy array format
          } else if (raw && raw.steps) {
            stepsJson  = raw.steps;                       // new combined format
            savedEdges = raw.edges?.length ? raw.edges : null;
            // Positions from source_steps may be stale (computed with a prior layout
            // algorithm). Use the dedicated graph_positions column instead — it is only
            // populated when the user explicitly saves from the editor, so it reflects
            // deliberate manual arrangements rather than auto-generated layout artefacts.
            savedPositions = null; // raw.positions intentionally ignored
          } else {
            stepsJson = dbRunbookToEditorSteps(rb);
          }
        } else {
          stepsJson = dbRunbookToEditorSteps(rb);
        }

        const syntheticJSON = JSON.stringify({
          name: rb.name,
          trigger_type: rb.event_type || rb.trigger_type || '',
          steps: stepsJson,
        });
        const result = parseRunbookJSON(syntheticJSON);
        if (typeof result !== 'string') {
          // Restore edges with correct routing (sourceHandle), unique IDs, and visual style.
          // When savedEdges exist, re-run BFS layout against them (clean graph edges)
          // rather than parseRunbookJSON's mixed sequential+decision edges, which cause
          // decision branch targets to appear as convergence nodes and collapse to col 0.
          if (savedEdges) {
            const styledSaved = savedEdges.map((e: any) => {
              const sh: string | undefined = e.sourceHandle ?? undefined;
              const id = e.id || `e-${e.source}-${e.target}${sh ? '-' + sh : ''}`;
              return {
                ...e, id, sourceHandle: sh,
                style: { stroke: sh === 'true' ? '#10b981' : sh === 'false' ? '#f43f5e' : '#2d3450', strokeWidth: 2, strokeDasharray: sh === 'false' ? '5 3' : undefined },
                label:        sh === 'true' ? 'true' : sh === 'false' ? 'false' : '',
                labelStyle:   { fill: sh === 'true' ? '#10b981' : sh === 'false' ? '#f43f5e' : '#7c85a0', fontSize: 10 },
                labelBgStyle: { fill: sh === 'true' ? '#0d3025' : sh === 'false' ? '#3d0a14' : '#13161f' },
              };
            });
            // Always run the layout algorithm for correct topology, then apply
            // user-saved positions (graph_positions column) as explicit overrides.
            const laidOut = computeBFSLayout(result.nodes as Node<RunbookStepData>[], styledSaved);
            const explicitPos: Record<string, { x: number; y: number }> | null =
              rb.graph_positions && Object.keys(rb.graph_positions).length ? rb.graph_positions : null;
            const restoredNodes = explicitPos
              ? laidOut.map(n => ({ ...n, position: explicitPos[n.id] || n.position }))
              : laidOut;
            setNodes(restoredNodes as any);
            setEdges(styledSaved);
          } else {
            setNodes(result.nodes as any);
            setEdges(result.edges);
          }
          setPendingFitView(true);
        }
      })
      .catch(err => console.error('[LOAD RUNBOOK]', err))
      .finally(() => setLoadingRunbook(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Node/edge operations ──────────────────────────────────────────────────

  const deleteNode = useCallback((id: string) => {
    pushSnapshotRef.current();
    setNodes(ns => ns.filter(n => n.id !== id));
    setEdges(es => es.filter(e => e.source !== id && e.target !== id));
    setSelectedNodeId(null);
  }, [setNodes, setEdges]);

  const deleteEdge = useCallback((id: string) => {
    pushSnapshotRef.current();
    setEdges(es => es.filter(e => e.id !== id));
    setSelectedEdgeId(null);
  }, [setEdges]);

  const updateNode = useCallback((id: string, patch: Partial<RunbookStepData>) => {
    setNodes(ns => ns.map(n => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n));
  }, [setNodes]);

  // ── Keyboard Delete (nodes + edges) ─────────────────────────────────────

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const inInput = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;

      // ── Ctrl+Z / Ctrl+Shift+Z: Undo / Redo ──────────────────────────────
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        if (inInput) return; // let native undo work in text fields
        e.preventDefault();
        if (e.shiftKey) {
          // Redo
          if (!futureRef.current.length) return;
          historyRef.current = [...historyRef.current, { nodes, edges }];
          const next = futureRef.current[0];
          futureRef.current = futureRef.current.slice(1);
          setNodes(next.nodes);
          setEdges(next.edges);
          setSelectedNodeId(null); setSelectedEdgeId(null);
        } else {
          // Undo
          if (!historyRef.current.length) return;
          futureRef.current = [{ nodes, edges }, ...futureRef.current.slice(0, 49)];
          const prev = historyRef.current[historyRef.current.length - 1];
          historyRef.current = historyRef.current.slice(0, -1);
          setNodes(prev.nodes);
          setEdges(prev.edges);
          setSelectedNodeId(null); setSelectedEdgeId(null);
        }
        return;
      }

      // ── Delete / Backspace: remove selected node or edge ─────────────────
      if (e.key !== 'Delete' && e.key !== 'Backspace') return;
      if (inInput) return;
      if (selectedNodeId && !['start', 'end'].includes(selectedNodeId)) { deleteNode(selectedNodeId); return; }
      if (selectedEdgeId) { deleteEdge(selectedEdgeId); return; }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selectedNodeId, selectedEdgeId, deleteNode, deleteEdge, nodes, edges, setNodes, setEdges]);

  // ── Auto-fit after async node load ───────────────────────────────────────

  useEffect(() => {
    if (!pendingFitView || !rfInstance) return;
    // Small delay lets React flush the new nodes to the DOM before fitting
    const t = setTimeout(() => {
      rfInstance.fitView({ padding: 0.12, duration: 350 });
      setPendingFitView(false);
    }, 80);
    return () => clearTimeout(t);
  }, [pendingFitView, rfInstance]);

  // ── Save to backend ──────────────────────────────────────────────────────

  const saveRunbook = async () => {
    const errs = validateGraph();
    setShowValidation(true);

    // Mandatory runbook-level fields block save entirely
    if (errs.has('__name') || errs.has('__event_type') || errs.has('__action')) {
      return;
    }

    // Node-level issues warn but allow the user to override
    const nodeErrs = new Map([...errs].filter(([k]) => !k.startsWith('__')));
    if (nodeErrs.size > 0) {
      const proceed = window.confirm(
        `The graph has ${nodeErrs.size} validation issue${nodeErrs.size > 1 ? 's' : ''}.\n\nSave anyway?`
      );
      if (!proceed) return;
    }
    setSaveStatus('saving');
    setSaveError('');
    const json = buildJson(runbookName, triggerType, nodes as Node<RunbookStepData>[], edges, rbMeta, confidence);
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const tok = localStorage.getItem('ap_token') || authToken;
    if (tok) headers['Authorization'] = `Bearer ${tok}`;
    try {
      // Include graph edges so the backend can persist decision routing.
      // Only the fields the executor/DB need — strip ReactFlow visual properties.
      // IMPORTANT: preserve null sourceHandle for regular edges — passing 'default'
      // breaks restoration because regular StepNodes have no id='default' handle
      // (only decision nodes do). null → omitted from JSON → ReactFlow connects
      // to the unid'd default handle correctly on reload.
      const edgePayload = edges.map(e => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle ?? null,
      }));
      // Save node positions so the canvas layout is restored exactly on reload.
      const positionPayload: Record<string, { x: number; y: number }> = {};
      (nodes as Node<RunbookStepData>[]).forEach(n => { positionPayload[n.id] = n.position; });
      const body = JSON.stringify({ ...json, source: rbSource || undefined, generation_prompt: generationPrompt || undefined, graph_edges: edgePayload, graph_positions: positionPayload });

      let res: Response;
      if (editingId) {
        // Update existing runbook
        res = await fetch(`${API_BASE}/runbooks/${editingId}`, {
          method: 'PUT',
          headers,
          body,
        });
      } else {
        // Create new runbook
        res = await fetch(`${API_BASE}/runbooks`, {
          method: 'POST',
          headers,
          body,
        });
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        console.error('[SAVE]', err);
        const detail = err?.detail ?? res.statusText ?? 'Save failed';
        setSaveError(`Save failed (${res.status}): ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
        setSaveStatus('error');
      } else {
        const saved = await res.json();
        setSaveStatus('saved');
        setRbStatus(saved.status === 'published' ? 'published' : 'draft');
        setHasUnpublishedChanges(!!saved.has_unpublished_changes);
        // After first POST, update URL and set editingId so subsequent saves use PUT
        if (!editingId && saved.runbook_id) {
          setEditingId(saved.runbook_id);
          const url = new URL(window.location.href);
          url.searchParams.set('id', saved.runbook_id);
          window.history.replaceState({}, '', url.toString());
        }
      }
    } catch (e) {
      console.error('[SAVE]', e);
      setSaveError(e instanceof Error ? `Save failed: ${e.message}` : 'Save failed: could not reach the server');
      setSaveStatus('error');
    }
    setTimeout(() => setSaveStatus('idle'), 2500);
  };

  // Promote the current draft to live — only meaningful once the runbook has
  // been saved at least once (editingId set). Mirrors saveRunbook's fetch pattern.
  const publishRunbook = async () => {
    if (!editingId) return;
    setPublishStatus('publishing');
    setSaveError('');
    setPublishWarnings([]);
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const tok = localStorage.getItem('ap_token') || authToken;
    if (tok) headers['Authorization'] = `Bearer ${tok}`;
    try {
      const res = await fetch(`${API_BASE}/runbooks/${editingId}/publish`, { method: 'POST', headers, body: '{}' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        setSaveError(`Publish failed (${res.status}): ${typeof err?.detail === 'string' ? err.detail : res.statusText}`);
        setPublishStatus('error');
      } else {
        const published = await res.json();
        setRbStatus('published');
        setHasUnpublishedChanges(!!published.has_unpublished_changes);
        setPublishStatus('published');
        if (Array.isArray(published.warnings) && published.warnings.length > 0) {
          setPublishWarnings(published.warnings);
        }
      }
    } catch (e) {
      setSaveError(e instanceof Error ? `Publish failed: ${e.message}` : 'Publish failed: could not reach the server');
      setPublishStatus('error');
    }
    setTimeout(() => setPublishStatus('idle'), 2500);
  };

  // ── Live execute ──────────────────────────────────────────────────────────

  const executeLive = async () => {
    if (!liveTarget.trim()) return;
    setLiveRunning(true); setLiveError(''); setLiveResults(null);
    setDisplayedResults([]); setLiveRunningStep(null);
    // Deselect any selected node/edge so the right panel flips to results
    // immediately instead of staying on Properties until the canvas is clicked.
    setSelectedNodeId(null); setSelectedEdgeId(null);
    const json = buildJson(runbookName, triggerType, nodes as Node<RunbookStepData>[], edges, rbMeta, confidence);
    const hdrs: Record<string, string> = { 'Content-Type': 'application/json' };
    const tok = localStorage.getItem('ap_token') || authToken;
    if (tok) hdrs['Authorization'] = `Bearer ${tok}`;
    try {
      // Build incident_context from test-run fields — only include non-empty values
      const incidentContext: Record<string, string> = {};
      if (ctxServiceUrl.trim())  incidentContext['service_url']    = ctxServiceUrl.trim();
      if (ctxProcessName.trim()) incidentContext['process_name']   = ctxProcessName.trim();
      if (ctxAnomalyProc.trim()) incidentContext['anomaly_process'] = ctxAnomalyProc.trim();

      const res = await fetch(`${API_BASE}/runbooks/execute-editor`, {
        method: 'POST',
        headers: hdrs,
        body: JSON.stringify({
          steps: json.steps,
          // Send graph edges so the backend can follow decision branches correctly.
          // Only the fields the executor needs — strip visual-only style/label data.
          edges: edges.map(e => ({ source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? 'default' })),
          target: liveTarget.trim(),
          watcher_name: liveWatcher.trim() || 'watcher_brain',
          on_step_failure: onStepFailure,
          incident_context: incidentContext,
          dry_run: dryRun,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        setLiveError(err.detail || `HTTP ${res.status}`);
      } else {
        const data = await res.json() as LiveResult;
        setLiveResults(data);
        // Replay results as visual progress on the graph
        if (data.results?.length) animateLiveResults(data.results);
      }
    } catch (e: any) {
      setLiveError(e.message || 'Network error — is the backend reachable?');
    } finally {
      setLiveRunning(false);
    }
  };

  // ── Reset simulation ──────────────────────────────────────────────────────

  const resetSim = useCallback(() => {
    setNodes((ns: any[]) => ns.map((n: any) => ({ ...n, data: { ...n.data, status: undefined, liveOutput: {} } })));
    setEdges((es: Edge[]) => es.map(e => ({ ...e, animated: false, style: { ...e.style, opacity: 1, strokeWidth: 2 } })));
    setSimLogs([]); setSimDone(false); setSimRunning(false);
  }, [setNodes, setEdges]);

  // ── Animate test-run results on the graph + results panel ────────────────
  //
  // Sequential two-phase replay:
  //   Phase 1 (base + 0ms)      — mark node "running", show step in panel as running
  //   Phase 2 (base + STEP_MS)  — settle node to final status, append result to panel
  //
  // Steps are NOT overlapped — each step fully settles before the next starts.
  const animateLiveResults = useCallback((results: StepResult[]) => {
    const STEP_MS = 850;   // how long each step shows as "running"
    const GAP_MS  = 180;   // breathing room between steps

    // Reset graph baseline
    setNodes((ns: any[]) => ns.map((n: any) => (
      ['start', 'end'].includes(n.data?.stepType) ? n
        : { ...n, data: { ...n.data, status: 'pending' as const, liveOutput: {} } }
    )));
    setEdges((es: Edge[]) => es.map(e => ({ ...e, animated: false, style: { ...e.style, opacity: 0.3 } })));

    // Clear panel — results will grow in one at a time
    setDisplayedResults([]);
    setLiveRunningStep(null);

    results.forEach((r, i) => {
      const base = i * (STEP_MS + GAP_MS);
      // The node the executor actually came from for this step — needed to
      // disambiguate which edge to animate when two branches (e.g. a
      // decision's true and false routes) converge on the same downstream
      // node. Matching by target alone would animate every edge pointing at
      // that node, lighting up both the taken and untaken branch together.
      const prevNodeId = i > 0 ? results[i - 1].node_id : null;

      // ── Phase 1: running ────────────────────────────────────────────────────
      setTimeout(() => {
        setLiveRunningStep(r);
        if (r.node_id) {
          setNodes((ns: any[]) => ns.map((n: any) =>
            n.id !== r.node_id ? n : { ...n, data: { ...n.data, status: 'running' as const } }
          ));
        }
      }, base);

      // ── Phase 2: settle ─────────────────────────────────────────────────────
      setTimeout(() => {
        const status = r.skipped ? 'skipped' : r.success ? 'success' : 'failed';
        // Append this result to the panel and clear the running indicator
        setDisplayedResults(prev => [...prev, r]);
        setLiveRunningStep(null);
        if (r.node_id) {
          setNodes((ns: any[]) => ns.map((n: any) =>
            n.id !== r.node_id ? n
              : { ...n, data: { ...n.data, status: status as any, liveOutput: r.structured || {} } }
          ));
          setEdges((es: Edge[]) => es.map(e => {
            if (e.target !== r.node_id) return e;
            if (prevNodeId && e.source !== prevNodeId) return e;
            // Animate for any step that actually ran, success or failed —
            // only a skipped step (branch not taken) should leave the edge
            // dimmed and still.
            return { ...e, animated: !r.skipped, style: { ...e.style, opacity: r.skipped ? 0.2 : 1 } };
          }));
        }
      }, base + STEP_MS);
    });
  }, [setNodes, setEdges]);

  // ── Graph validation ──────────────────────────────────────────────────────

  const validateGraph = useCallback((): Map<string, string> => {
    const nodeArr = nodes as Node<RunbookStepData>[];
    const errors  = new Map<string, string>();

    // ── Runbook-level mandatory checks ────────────────────────────────────────
    if (!runbookName.trim())  errors.set('__name',       'Runbook name is required');
    if (!triggerType.trim())  errors.set('__event_type', 'Event type is required');
    const hasAction = nodeArr.some(n => n.data.stepType === 'action');
    if (!hasAction)           errors.set('__action',     'At least one Action step is required');

    // ── Graph node checks ─────────────────────────────────────────────────────
    const incoming = new Map<string, number>();
    const outgoing = new Map<string, number>();
    const trueOut  = new Map<string, number>();
    const falseOut = new Map<string, number>();
    nodeArr.forEach(n => { incoming.set(n.id, 0); outgoing.set(n.id, 0); });
    edges.forEach(e => {
      incoming.set(e.target, (incoming.get(e.target) ?? 0) + 1);
      outgoing.set(e.source, (outgoing.get(e.source) ?? 0) + 1);
      if (e.sourceHandle === 'true')  trueOut.set(e.source,  (trueOut.get(e.source)  ?? 0) + 1);
      if (e.sourceHandle === 'false') falseOut.set(e.source, (falseOut.get(e.source) ?? 0) + 1);
    });

    nodeArr.forEach(n => {
      const d = n.data;
      if (d.stepType === 'start' || d.stepType === 'end') return;
      const msgs: string[] = [];

      // Decision nodes use their condition as identity — name not required
      if (!d.name?.trim() && d.stepType !== 'decision') msgs.push('Missing name');

      if (d.stepType === 'decision') {
        if (!d.condition?.trim()) msgs.push('Missing condition');
        if (!(trueOut.get(n.id) ?? 0))  msgs.push('No TRUE route connected');
        if (!(falseOut.get(n.id) ?? 0)) msgs.push('No FALSE route connected');
      } else if (d.stepType !== 'notify' && d.stepType !== 'wait' && d.stepType !== 'incident_update') {
        // 'wait' and 'incident_update' are built-in step types with no tool — skip tool check
        if (!d.tool?.trim()) msgs.push('Missing tool');
      }

      if (!(incoming.get(n.id) ?? 0)) msgs.push('No incoming connection');
      if (!(outgoing.get(n.id) ?? 0)) msgs.push('No outgoing connection');

      if (msgs.length) errors.set(n.id, msgs.join(' · '));
    });

    setValidationErrors(errors);
    return errors;
  }, [nodes, edges, runbookName, triggerType]);

  // ── Graph interactions ────────────────────────────────────────────────────

  const onConnect = useCallback((params: Connection) => {
    pushSnapshotRef.current();
    const color = params.sourceHandle === 'true' ? '#10b981' : params.sourceHandle === 'false' ? '#f43f5e' : '#2d3450';
    setEdges(es => addEdge({ ...params, style: { stroke: color, strokeWidth: 2, strokeDasharray: params.sourceHandle === 'false' ? '5 3' : undefined }, label: params.sourceHandle === 'true' ? 'true' : params.sourceHandle === 'false' ? 'false' : '', labelStyle: { fill: color, fontSize: 10 }, labelBgStyle: { fill: params.sourceHandle === 'true' ? '#0d3025' : params.sourceHandle === 'false' ? '#3d0a14' : '#13161f' } }, es));
  }, [setEdges]);

  const onNodeClick  = useCallback((_: any, node: Node) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
    // Selecting a node shows Properties (it takes priority over the results
    // panel — see the render below), but we keep the run data intact so
    // clicking back to the canvas (onPaneClick) reveals the same results
    // again without having to re-run the test.
  }, []);
  const onEdgeClick  = useCallback((_: any, edge: Edge) => { setSelectedEdgeId(edge.id); setSelectedNodeId(null); }, []);
  const onEdgeDblClick = useCallback((_: any, edge: Edge) => deleteEdge(edge.id), [deleteEdge]);
  const onPaneClick  = useCallback(() => { setSelectedNodeId(null); setSelectedEdgeId(null); }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const type = e.dataTransfer.getData('stepType') as StepType;
    const tool = e.dataTransfer.getData('tool');
    if (!type || !wrapRef.current || !rfInstance) return;
    const bounds = wrapRef.current.getBoundingClientRect();
    const position = rfInstance.screenToFlowPosition({ x: e.clientX - bounds.left, y: e.clientY - bounds.top });
    const id = newId(type);

    // Find approved action params if tool was dragged from sidebar
    let defaultArgs: Record<string, string> = {};
    if (tool) {
      const action = approvedActions.find(a => a.tool_name === tool);
      if (action && Array.isArray(action.parameters)) {
        defaultArgs = Object.fromEntries(
          action.parameters
            .filter(p => p.name !== 'target')
            .map(p => [p.name, p.default ?? ''])
        );
      }
    }

    pushSnapshotRef.current();
    setNodes(nds => [...nds, {
      id, type: 'step', position,
      data: { id, stepType: type, name: tool ? tool.replace(/_/g, ' ') : '', tool: tool || '', args: defaultArgs },
    }]);
    setSelectedNodeId(id); setSelectedEdgeId(null);
  }, [rfInstance, setNodes, approvedActions]);

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; };

  // ── Simulation ────────────────────────────────────────────────────────────

  const runSim = () => {
    setSimLogs([]); setSimDone(false); setSimRunning(true); setShowSimBar(false);
    runSimulation(nodes as Node<RunbookStepData>[], edges, setNodes, setEdges, parseInt(simDisk) || 87,
      log => setSimLogs(prev => [...prev, log]),
      () => { setSimDone(true); setSimRunning(false); },
    );
  };
  const handleSimulate = () => { setView('graph'); setShowSimBar(true); };

  // ── Export JSON ───────────────────────────────────────────────────────────

  const exportJson = () => {
    const json = buildJson(runbookName, triggerType, nodes as Node<RunbookStepData>[], edges, rbMeta, confidence);
    const edgePayload = edges.map(e => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? null }));
    const positionPayload: Record<string, { x: number; y: number }> = {};
    (nodes as Node<RunbookStepData>[]).forEach(n => { positionPayload[n.id] = n.position; });
    const full = { ...json, graph_edges: edgePayload, graph_positions: positionPayload };
    const blob = new Blob([JSON.stringify(full, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${(runbookName || triggerType || 'runbook').replace(/[^a-z0-9_]/gi, '_')}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  // ── Import from file ─────────────────────────────────────────────────────

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = ''; // reset so the same file can be re-imported
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      const result = parseRunbookJSON(text);
      if (typeof result === 'string') { alert(`Import failed: ${result}`); return; }

      // Fast path: exported with full graph_edges + graph_positions — no reconstruction needed
      try {
        const json = JSON.parse(text);
        if (json.graph_edges && Array.isArray(json.graph_edges)) {
          const styledEdges: Edge[] = (json.graph_edges as any[]).map(e => ({
            ...e,
            style: { stroke: '#10b981', strokeWidth: 2 },
            animated: false,
          }));
          const finalNodes = json.graph_positions
            ? result.nodes.map(n => json.graph_positions[n.id] ? { ...n, position: json.graph_positions[n.id] } : n)
            : result.nodes;
          setNodes(finalNodes as any);
          setEdges(styledEdges);
        } else {
          setNodes(result.nodes as any);
          setEdges(result.edges);
        }
        // Restore all runbook metadata
        setRunbookName(json.name || result.name);
        setTriggerType(json.trigger_type || result.trigger);
        setDescription(json.description || '');
        setPlatform(json.platform || 'any');
        setService(json.service || '');
        setConfidence(typeof json.confidence === 'number' ? json.confidence : 0.80);
        setBlastRadius(typeof json.blast_radius === 'number' ? json.blast_radius : 2);
        setRbEnabled(json.enabled !== false);
      } catch {
        setNodes(result.nodes as any);
        setEdges(result.edges);
        setRunbookName(result.name);
        setTriggerType(result.trigger);
      }

      setEditingId(null);
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
      const url = new URL(window.location.href);
      url.searchParams.delete('id');
      window.history.replaceState({}, '', url.toString());
      resetSim();
    };
    reader.readAsText(file);
  };

  // ── AI Generate ──────────────────────────────────────────────────────────

  const doGenerate = async () => {
    if (!genDesc.trim()) return;
    setGenLoading(true); setGenError('');
    const hdrs: Record<string, string> = { 'Content-Type': 'application/json' };
    const tok = localStorage.getItem('ap_token') || authToken;
    if (tok) hdrs['Authorization'] = `Bearer ${tok}`;
    try {
      const res = await fetch(`${API_BASE}/runbooks/generate`, {
        method: 'POST',
        headers: hdrs,
        body: JSON.stringify({
          description: genDesc.trim(),
          event_type:  triggerType || '',
          platform:    platform    || 'any',
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        setGenError(err.detail || `HTTP ${res.status}`);
        return;
      }
      const generated = await res.json();
      // Inject graph_edges into the steps JSON and run through the importer
      const importable = JSON.stringify({
        ...generated,
        // parseRunbookJSON reads graph_edges for routing when available
        steps: generated.steps,
      });
      const result = parseRunbookJSON(importable);
      if (typeof result === 'string') { setGenError(result); return; }
      // Normalize each step's args against the tool's actual parameter definitions.
      // This auto-applies the same wiring that handleToolSelect does on manual pick,
      // so generated runbooks don't require the user to re-select each tool.
      const normalizedNodes = (result.nodes as any[]).map((n: any) => {
        const tool = n.data?.tool;
        if (!tool) return n;
        const stepType = n.data?.stepType;
        if (stepType === 'start' || stepType === 'end' || stepType === 'decision') return n;
        const action = approvedActions.find((a: ApprovedAction) => a.tool_name === tool);
        if (!action || !Array.isArray(action.parameters)) return n;
        const existingArgs = n.data.args || {};
        const newArgs: Record<string, string> = {};
        for (const p of action.parameters) {
          if (p.name === 'target') continue;
          newArgs[p.name] = existingArgs[p.name] ?? String(p.default ?? '');
        }
        return { ...n, data: { ...n.data, args: newArgs } };
      });
      // Apply saved edges with proper styles
      const styledEdges = (generated.graph_edges || result.edges).map((e: any) => {
        const sh: string | undefined = e.sourceHandle ?? undefined;
        const id = e.id || `e-${e.source}-${e.target}${sh ? '-' + sh : ''}`;
        return {
          ...e,
          id,
          sourceHandle: sh,
          style: {
            stroke: sh === 'true' ? '#10b981' : sh === 'false' ? '#f43f5e' : '#2d3450',
            strokeWidth: 2,
            strokeDasharray: sh === 'false' ? '5 3' : undefined,
          },
          label:        sh === 'true' ? 'true' : sh === 'false' ? 'false' : '',
          labelStyle:   { fill: sh === 'true' ? '#10b981' : sh === 'false' ? '#f43f5e' : '#7c85a0', fontSize: 10 },
          labelBgStyle: { fill: sh === 'true' ? '#0d3025' : sh === 'false' ? '#3d0a14' : '#13161f' },
        };
      });
      // Re-run layout with the ACTUAL displayed edges (generated.graph_edges may differ
      // from the edges parseRunbookJSON derived from on_true/on_false alone)
      const layoutedNodes = computeBFSLayout(normalizedNodes as Node<RunbookStepData>[], styledEdges);
      setNodes(layoutedNodes);
      setEdges(styledEdges);
      if (generated.name)        setRunbookName(generated.name);
      if (generated.trigger_type) setTriggerType(generated.trigger_type);
      if (generated.description)  setDescription(generated.description);
      if (generated.platform)     setPlatform(generated.platform);
      if (generated.blast_radius !== undefined) setBlastRadius(generated.blast_radius);
      setRbSource('ai_generated');
      setGenerationPrompt(generated.generation_prompt || '');
      setEditingId(null);
      const url = new URL(window.location.href);
      url.searchParams.delete('id');
      window.history.replaceState({}, '', url.toString());
      setSelectedNodeId(null); setSelectedEdgeId(null);
      resetSim();
      setPendingFitView(true);
      setShowGenerate(false); setGenDesc(''); setGenError('');
    } catch (e: any) {
      setGenError(e.message || 'Network error — is the backend reachable?');
    } finally {
      setGenLoading(false);
    }
  };

  // ── Display overlays (edge highlight + validation errors) ─────────────────

  // Boost selected edge strokeWidth + add glow so it's clearly visible
  const displayEdges = useMemo(() => {
    if (!selectedEdgeId) return edges;
    return edges.map(e => e.id !== selectedEdgeId ? e : {
      ...e,
      style: {
        ...e.style,
        strokeWidth: 4,
        filter: 'drop-shadow(0 0 6px rgba(124,58,237,0.85))',
      },
    });
  }, [edges, selectedEdgeId]);

  // Overlay hasError onto nodes that failed validation
  const displayNodes = useMemo(() => {
    if (validationErrors.size === 0) return nodes;
    return (nodes as Node<RunbookStepData>[]).map(n => ({
      ...n,
      data: { ...n.data, hasError: validationErrors.has(n.id) },
    }));
  }, [nodes, validationErrors]);

  // ── Render ────────────────────────────────────────────────────────────────

  const showSimPanel = simRunning || simDone;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: '#0d0f14', color: '#e2e8f0', fontFamily: 'Inter, system-ui, sans-serif' }}>

      {/* ── Loading overlay ── */}
      {loadingRunbook && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(13,15,20,.85)', zIndex: 999, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, color: '#7c85a0', gap: 10 }}>
          <span style={{ fontSize: 18 }}>⟳</span> Loading runbook…
        </div>
      )}

      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 16px', height: 52, background: '#13161f', borderBottom: '1px solid #1e2231', flexShrink: 0 }}>
        <a href="/" style={{ color: '#7c85a0', fontSize: 12, cursor: 'pointer', textDecoration: 'none' }}>← Library</a>
        <div style={{ width: 1, height: 22, background: '#1e2231' }} />
        <div style={{ fontSize: 14, fontWeight: 600, color: editingId ? '#e2e8f0' : '#94a3b8', flex: 1, display: 'flex', alignItems: 'center', gap: 8 }}>
          {runbookName || <span style={{ color: '#4a5068', fontStyle: 'italic' }}>Untitled Runbook</span>}
          {rbSource === 'ai_generated' && (
            <span
              style={{ fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4, color: '#a78bfa', background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.35)', letterSpacing: '0.04em' }}
              title="Generated by AI — review before relying on it"
            >
              AI
            </span>
          )}
          {editingId && <span style={{ fontSize: 10, color: '#4a5068', fontWeight: 400, fontFamily: 'monospace' }}>#{editingId.slice(0,8)}</span>}
        </div>
        <div style={{ background: '#1e2231', border: '1px solid #2d3450', color: '#7c85a0', fontSize: 11, padding: '2px 8px', borderRadius: 4, fontFamily: 'monospace' }}>{triggerType || '—'}</div>
        <div style={{ display: 'flex', gap: 2, background: '#0d0f14', border: '1px solid #1e2231', borderRadius: 7, padding: 3 }}>
          {(['graph', 'json'] as const).map(v => (
            <button key={v} onClick={() => setView(v)} style={{ padding: '4px 12px', borderRadius: 5, border: 'none', background: view === v ? '#1e2231' : 'transparent', color: view === v ? '#e2e8f0' : '#7c85a0', fontSize: 11, fontWeight: 500, cursor: 'pointer' }}>
              {v === 'graph' ? 'Graph' : 'JSON'}
            </button>
          ))}
        </div>
        {(liveResults || liveRunning || displayedResults.length > 0 || liveRunningStep) && <button onClick={() => { setLiveResults(null); setLiveError(''); setDisplayedResults([]); setLiveRunningStep(null); setNodes((ns: any[]) => ns.map((n: any) => ({ ...n, data: { ...n.data, status: undefined, liveOutput: {} } }))); setEdges((es: Edge[]) => es.map(e => ({ ...e, animated: false, style: { ...e.style, opacity: 1 } }))); }} style={hdrBtn('#1a1d26', '#94a3b8')}>Reset</button>}
        <button onClick={() => { setShowLiveBar(v => !v); setLiveResults(null); setLiveError(''); }} style={hdrBtn(showLiveBar ? '#1e2d4a' : 'transparent', showLiveBar ? '#60a5fa' : '#94a3b8', '1px solid #1a1d26')}>Test Run</button>
        <button onClick={() => { setShowGenerate(true); setGenError(''); if (rbSource === 'ai_generated' && generationPrompt) setGenDesc(generationPrompt); }} style={hdrBtn('#1a1040', '#a78bfa', '1px solid #3b1f7a')}>✦ Generate</button>
        <input ref={importFileRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleImportFile} />
        <button onClick={() => importFileRef.current?.click()} style={hdrBtn('#131a27', '#3b82f6', '1px solid #1e2d4a')}>Import</button>
        <button onClick={exportJson}                           style={hdrBtn('#131a27', '#3b82f6', '1px solid #1e2d4a')}>Export</button>
        <button onClick={() => {
          const errs = validateGraph();
          setShowValidation(true);
          if (errs.size === 0) setTimeout(() => setShowValidation(false), 2500);
        }} style={hdrBtn(validationErrors.size > 0 ? '#450a0a' : '#131a27', validationErrors.size > 0 ? '#f43f5e' : '#7c85a0', '1px solid #1a1d26')} title="Validate graph before saving">
          {validationErrors.size > 0 ? `⚠ ${validationErrors.size} issue${validationErrors.size > 1 ? 's' : ''}` : 'Validate'}
        </button>
        {editingId && (
          <span style={{
            fontSize: 10, fontWeight: 600, padding: '3px 8px', borderRadius: 5,
            ...(rbStatus === 'published'
              ? { background: 'rgba(16,185,129,0.12)', border: '1px solid rgba(16,185,129,0.35)', color: '#10b981' }
              : { background: 'rgba(148,163,184,0.12)', border: '1px solid rgba(148,163,184,0.35)', color: '#94a3b8' }),
          }}>
            {rbStatus === 'published' ? 'Published' : 'Draft'}
            {hasUnpublishedChanges && rbStatus === 'published' ? ' • unpublished' : ''}
          </span>
        )}
        <button onClick={saveRunbook} style={hdrBtn(saveStatus === 'saved' ? '#064e3b' : saveStatus === 'error' ? '#450a0a' : '#4c1d95', saveStatus === 'saved' ? '#10b981' : saveStatus === 'error' ? '#f43f5e' : 'white')}>
          {saveStatus === 'saving' ? '…' : saveStatus === 'saved' ? 'Saved ✓' : saveStatus === 'error' ? 'Error' : editingId ? 'Save Draft' : 'Save'}
        </button>
        {editingId && (
          <button
            onClick={publishRunbook}
            disabled={publishStatus === 'publishing' || (rbStatus === 'published' && !hasUnpublishedChanges)}
            title={rbStatus === 'published' && !hasUnpublishedChanges ? 'No pending changes to publish' : 'Make the current draft live'}
            style={hdrBtn(publishStatus === 'published' ? '#064e3b' : publishStatus === 'error' ? '#450a0a' : '#0f3d2e', publishStatus === 'published' ? '#10b981' : publishStatus === 'error' ? '#f43f5e' : '#34d399')}
          >
            {publishStatus === 'publishing' ? '…' : publishStatus === 'published' ? 'Published ✓' : publishStatus === 'error' ? 'Error' : 'Publish'}
          </button>
        )}
      </div>

      {/* ── Validation banner ── */}
      {showValidation && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 16px', background: validationErrors.size > 0 ? '#1a0a0a' : '#061a12', borderBottom: `1px solid ${validationErrors.size > 0 ? '#3d1010' : '#0d3025'}`, flexShrink: 0, flexWrap: 'wrap' }}>
          {validationErrors.size === 0 ? (
            <span style={{ fontSize: 11, color: '#10b981', fontWeight: 700 }}>✓ Graph is valid — no issues found</span>
          ) : (
            <>
              <span style={{ fontSize: 11, color: '#f43f5e', fontWeight: 700, flexShrink: 0 }}>⚠ {validationErrors.size} issue{validationErrors.size > 1 ? 's' : ''}:</span>
              {Array.from(validationErrors.entries()).map(([key, msg]) => {
                if (key.startsWith('__')) {
                  // Runbook-level error — not tied to a graph node
                  return (
                    <span key={key}
                      style={{ fontSize: 10, background: '#2d0a0a', border: '1px solid #5a1818', borderRadius: 4, padding: '2px 8px', color: '#fca5a5', flexShrink: 0 }}>
                      {msg}
                    </span>
                  );
                }
                const n = (nodes as Node<RunbookStepData>[]).find(x => x.id === key);
                return (
                  <button key={key} onClick={() => { setSelectedNodeId(key); setSelectedEdgeId(null); }}
                    style={{ fontSize: 10, background: '#2d0a0a', border: '1px solid #5a1818', borderRadius: 4, padding: '2px 8px', color: '#fca5a5', cursor: 'pointer', flexShrink: 0 }}
                    title={msg}>
                    <span style={{ fontWeight: 700 }}>{n?.data?.name || key}</span>: {msg}
                  </button>
                );
              })}
            </>
          )}
          <div style={{ flex: 1 }} />
          <button onClick={() => setShowValidation(false)} style={{ background: 'none', border: 'none', color: '#475569', fontSize: 14, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
      )}

      {/* ── Save error banner — persists until dismissed or the next successful save,
             independent of the Save button's 2.5s status flash, so the actual cause
             (e.g. a 422 from the backend) is actually readable. ── */}
      {saveError && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 16px', background: '#1a0a0a', borderBottom: '1px solid #3d1010', flexShrink: 0, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: '#f43f5e', fontWeight: 700, flexShrink: 0 }}>⚠</span>
          <span style={{ fontSize: 11, color: '#fca5a5' }}>{saveError}</span>
          <div style={{ flex: 1 }} />
          <button onClick={() => setSaveError('')} style={{ background: 'none', border: 'none', color: '#475569', fontSize: 14, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
      )}

      {/* ── Publish lint warnings — advisory only, publish already succeeded ── */}
      {publishWarnings.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '7px 16px', background: '#1c1500', borderBottom: '1px solid #3d2e10', flexShrink: 0, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: '#f59e0b', fontWeight: 700, flexShrink: 0 }}>⚠</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 11, color: '#fbbf24', fontWeight: 600 }}>Published — but with a path that can't auto-resolve:</div>
            {publishWarnings.map((w, i) => (
              <div key={i} style={{ fontSize: 10.5, color: '#fcd34d', opacity: 0.9, marginTop: 2 }}>{w}</div>
            ))}
          </div>
          <button onClick={() => setPublishWarnings([])} style={{ background: 'none', border: 'none', color: '#475569', fontSize: 14, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
      )}

      {/* ── Live Execute bar ── */}
      {showLiveBar && (
        <div style={{ background: '#13161f', borderBottom: '1px solid #1e2231', flexShrink: 0 }}>
          {/* Row 1: watcher / target / on-fail / run button */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 20px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, color: '#94a3b8', fontWeight: 700, flexShrink: 0, letterSpacing: '.04em' }}>TEST RUN</span>
            <div style={{ width: 1, height: 16, background: '#1e2231' }} />

            {/* Watcher dropdown */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11, color: '#4a5068' }}>Watcher:</span>
              {watchers.length > 0 ? (
                <select value={liveWatcher} onChange={e => { const w = watchers.find(w => w.watcher_name === e.target.value); setLiveWatcher(e.target.value); if (w) setLiveTarget(w.sentinel_container || ''); }}
                  style={{ background: '#161b28', border: '1px solid #2a3548', borderRadius: 5, padding: '4px 8px', color: '#e2e8f0', fontSize: 12, outline: 'none', cursor: 'pointer' }}>
                  {watchers.map(w => <option key={w.watcher_name} value={w.watcher_name}>{w.display_name || w.watcher_name}</option>)}
                </select>
              ) : (
                <input value={liveWatcher} onChange={e => setLiveWatcher(e.target.value)} placeholder="watcher_brain"
                  style={{ width: 140, background: '#161b28', border: '1px solid #2a3548', borderRadius: 5, padding: '4px 8px', color: '#e2e8f0', fontSize: 12, fontFamily: 'monospace', outline: 'none' }} />
              )}
            </div>

            {/* Target container */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11, color: '#4a5068' }}>Target:</span>
              <input value={liveTarget} onChange={e => setLiveTarget(e.target.value)} placeholder="container name"
                style={{ width: 200, background: '#161b28', border: '1px solid #2a3548', borderRadius: 5, padding: '4px 8px', color: '#e2e8f0', fontSize: 12, fontFamily: 'monospace', outline: 'none' }} />
            </div>

            {/* On-failure policy */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11, color: '#4a5068', flexShrink: 0 }}>On fail:</span>
              <select value={onStepFailure} onChange={e => setOnStepFailure(e.target.value as typeof onStepFailure)}
                style={{ background: '#161b28', border: '1px solid #2a3548', borderRadius: 5, padding: '4px 8px', color: onStepFailure === 'continue' ? '#7c85a0' : onStepFailure === 'stop_actions' ? '#f59e0b' : '#f43f5e', fontSize: 11, outline: 'none', cursor: 'pointer' }}>
                <option value="continue">Continue all</option>
                <option value="stop_actions">Stop actions</option>
                <option value="stop_all">Stop all</option>
              </select>
            </div>

            {/* Context params toggle */}
            <button onClick={() => setShowCtxFields(v => !v)}
              style={{ padding: '4px 10px', borderRadius: 5, background: showCtxFields ? '#1e2d4a' : 'transparent', border: `1px solid ${(ctxServiceUrl || ctxProcessName || ctxAnomalyProc) ? '#3b82f6' : '#2a3548'}`, color: (ctxServiceUrl || ctxProcessName || ctxAnomalyProc) ? '#60a5fa' : '#4a5068', fontSize: 11, cursor: 'pointer', flexShrink: 0 }}
              title="Set incident context values (service_url, process_name, etc.)">
              {(ctxServiceUrl || ctxProcessName || ctxAnomalyProc) ? '⚙ Context ●' : '⚙ Context'}
            </button>

            {liveError && <span style={{ fontSize: 11, color: '#f43f5e' }}>{liveError}</span>}
            <div style={{ flex: 1 }} />
            <button onClick={() => setDryRun(v => !v)}
              title="Dry run: diagnostics execute normally; action steps are skipped and show resolved args"
              style={{ padding: '4px 10px', borderRadius: 5, background: dryRun ? '#422006' : 'transparent', border: `1px solid ${dryRun ? '#f59e0b' : '#2a3548'}`, color: dryRun ? '#f59e0b' : '#4a5068', fontSize: 11, cursor: 'pointer', flexShrink: 0 }}>
              {dryRun ? 'Dry Run ON' : 'Dry Run'}
            </button>
            <button onClick={executeLive} disabled={liveRunning || !liveTarget.trim()}
              style={{ padding: '5px 16px', borderRadius: 6, background: liveRunning ? '#1e2231' : dryRun ? '#854d0e' : '#3b82f6', border: dryRun ? '1px solid #f59e0b' : 'none', color: liveRunning ? '#4a5068' : '#fff', fontSize: 11, fontWeight: 600, cursor: liveRunning ? 'default' : 'pointer' }}>
              {liveRunning ? 'Running…' : dryRun ? 'Dry Run' : 'Run on Target'}
            </button>
            <button onClick={() => { setShowLiveBar(false); setLiveResults(null); setLiveError(''); }} style={{ padding: '5px 10px', borderRadius: 6, background: 'transparent', border: '1px solid #1e2231', color: '#475569', fontSize: 11, cursor: 'pointer' }}>×</button>
          </div>

          {/* Row 2: context parameter fields (collapsible) */}
          {showCtxFields && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 20px 8px', borderTop: '1px solid #1a1d26', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 10, color: '#334155', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.08em', flexShrink: 0 }}>Incident Context</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ fontSize: 11, color: '#4a5068' }}>service_url</span>
                <input value={ctxServiceUrl} onChange={e => setCtxServiceUrl(e.target.value)}
                  placeholder="http://my-service:8080"
                  style={{ width: 200, background: '#161b28', border: '1px solid #2a3548', borderRadius: 5, padding: '3px 7px', color: '#e2e8f0', fontSize: 11, fontFamily: 'monospace', outline: 'none' }} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ fontSize: 11, color: '#4a5068' }}>process_name</span>
                <input value={ctxProcessName} onChange={e => setCtxProcessName(e.target.value)}
                  placeholder="nginx"
                  style={{ width: 130, background: '#161b28', border: '1px solid #2a3548', borderRadius: 5, padding: '3px 7px', color: '#e2e8f0', fontSize: 11, fontFamily: 'monospace', outline: 'none' }} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ fontSize: 11, color: '#4a5068' }}>anomaly_process</span>
                <input value={ctxAnomalyProc} onChange={e => setCtxAnomalyProc(e.target.value)}
                  placeholder="python3"
                  style={{ width: 130, background: '#161b28', border: '1px solid #2a3548', borderRadius: 5, padding: '3px 7px', color: '#e2e8f0', fontSize: 11, fontFamily: 'monospace', outline: 'none' }} />
              </div>
              <span style={{ fontSize: 10, color: '#334155' }}>These fill {`{service_url}`}, {`{process_name}`}, {`{{top_process_name}}`} placeholders in step args</span>
            </div>
          )}
        </div>
      )}

      {/* ── Main ── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Sidebar
          runbookName={runbookName} triggerType={triggerType}
          onNameChange={setRunbookName} onTriggerChange={setTriggerType}
          description={description} onDescriptionChange={setDescription}
          platform={platform} onPlatformChange={setPlatform}
          service={service} onServiceChange={setService}
          blastRadius={blastRadius} onBlastRadiusChange={setBlastRadius}
          enabled={rbEnabled} onEnabledChange={setRbEnabled}
          nodeCount={nodes.length} edgeCount={edges.length}
          approvedActions={approvedActions}
          nameError={validationErrors.has('__name')}
          eventTypeError={validationErrors.has('__event_type')}
          noActionError={validationErrors.has('__action')}
        />

        <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', inset: 0, display: view === 'graph' ? 'flex' : 'none' }}>
            <div ref={wrapRef} style={{ width: '100%', height: '100%' }}>
              <ReactFlow
                nodes={displayNodes as any} edges={displayEdges}
                onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onNodeClick={onNodeClick} onEdgeClick={onEdgeClick} onEdgeDoubleClick={onEdgeDblClick}
                onPaneClick={onPaneClick}
                onDrop={onDrop} onDragOver={onDragOver} onInit={setRfInstance}
                nodeTypes={nodeTypes}
                style={{ background: '#0d0f14' }}
                defaultEdgeOptions={{ style: { stroke: '#2d3450', strokeWidth: 2 } }}
              >
                <Background variant={BackgroundVariant.Dots} color="#1e2231" gap={24} size={1} />
                <Controls style={{ background: '#13161f', border: '1px solid #1e2231', borderRadius: 8 }} />
                <MiniMap style={{ background: '#13161f', border: '1px solid #1e2231', borderRadius: 8 }}
                  nodeColor={n => {
                    const t = (n as Node<RunbookStepData>).data?.stepType;
                    return t === 'diagnostic' ? '#1e3a5f' : t === 'action' ? '#3d2800' : t === 'verification' ? '#0d3025' : t === 'decision' ? '#3d0a14' : '#2e1a5c';
                  }} />
                <Panel position="top-right" style={{ background: '#13161f', border: '1px solid #1e2231', borderRadius: 8, padding: '7px 12px', fontSize: 11, color: '#4a5068' }}>
                  Drag to add · Click to edit · Del to delete · Dbl-click edge to remove · Ctrl+Z undo
                </Panel>
              </ReactFlow>
            </div>
          </div>
          {view === 'json' && (() => {
            const edgePayload = edges.map(e => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? null }));
            const positionPayload: Record<string, { x: number; y: number }> = {};
            (nodes as Node<RunbookStepData>[]).forEach(n => { positionPayload[n.id] = n.position; });
            return <JsonPanel json={{ ...runbook, graph_edges: edgePayload, graph_positions: positionPayload }} />;
          })()}
        </div>

        {/* Right panel — Properties takes priority when a node is selected;
            Live results shown otherwise if a run has completed. */}
        {selectedNode ? (
          <PropertiesPanel
            node={{ id: selectedNode.id, data: selectedNode.data }}
            allNodeIds={nodes.filter((n: any) => !['start', 'end'].includes(n.data?.stepType as string)).map((n: any) => n.id as string)}
            allNodes={nodes as Node<RunbookStepData>[]}
            edges={edges}
            onChange={updateNode} onDelete={deleteNode}
            approvedActions={approvedActions}
          />
        ) : (liveResults || liveRunning || displayedResults.length > 0 || liveRunningStep) ? (
          <LiveResultPanel
            result={liveResults}
            displayedResults={displayedResults}
            runningStep={liveRunningStep}
            running={liveRunning}
            onClose={() => { setLiveResults(null); setLiveError(''); setDisplayedResults([]); setLiveRunningStep(null); }}
          />
        ) : (
          <PropertiesPanel
            node={null}
            allNodeIds={nodes.filter((n: any) => !['start', 'end'].includes(n.data?.stepType as string)).map((n: any) => n.id as string)}
            allNodes={nodes as Node<RunbookStepData>[]}
            edges={edges}
            onChange={updateNode} onDelete={deleteNode}
            approvedActions={approvedActions}
          />
        )}
      </div>

      {/* ── Generate Modal ── */}
      {showGenerate && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.8)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: '#0d1018', border: '1px solid #3b1f7a', borderRadius: 14, padding: 28, width: 600, display: 'flex', flexDirection: 'column', gap: 0, boxShadow: '0 24px 72px rgba(0,0,0,.7)', position: 'relative', overflow: 'hidden' }}>

            {/* loading overlay */}
            {genLoading && (
              <div style={{ position: 'absolute', inset: 0, background: 'rgba(13,16,24,0.88)', zIndex: 10, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14, borderRadius: 14 }}>
                <div style={{ width: 36, height: 36, border: '3px solid #3b1f7a', borderTop: '3px solid #a78bfa', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
                <div style={{ fontSize: 13, color: '#a78bfa', fontWeight: 600 }}>Generating your runbook…</div>
                <div style={{ fontSize: 11, color: '#475569' }}>This usually takes 5–15 seconds</div>
              </div>
            )}

            {/* header */}
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>✦ Generate Runbook with AI</div>
                <div style={{ fontSize: 11, color: '#475569', marginTop: 3 }}>Describe the issue or the steps you want — the AI will produce a decision-graph runbook using your approved tools.</div>
              </div>
              <button onClick={() => { setShowGenerate(false); setGenDesc(''); setGenError(''); }} style={{ background: 'none', border: 'none', color: '#475569', fontSize: 18, cursor: 'pointer', lineHeight: 1, marginLeft: 12 }}>×</button>
            </div>

            {/* description */}
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: '#334155', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 6 }}>What do you want the runbook to handle?</div>
              <textarea
                value={genDesc}
                onChange={e => { setGenDesc(e.target.value); setGenError(''); }}
                placeholder={"Examples:\n• Service is unresponsive — check container, memory pressure, and deadlock before escalating\n• High error rate — distinguish dependency failure, traffic spike, and bad deploy\n• Disk full — try log rotation, then tmp cleanup, escalate if still over 90%"}
                rows={6}
                autoFocus
                style={{ width: '100%', background: '#161b28', border: `1px solid ${genError ? '#f43f5e' : '#2a3548'}`, borderRadius: 8, padding: '12px 14px', fontSize: 12, color: '#e2e8f0', fontFamily: 'Inter, system-ui, sans-serif', outline: 'none', resize: 'vertical', lineHeight: 1.6, boxSizing: 'border-box' }}
              />
            </div>

            {/* optional fields row */}
            <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
              <div style={{ flex: 2 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: '#334155', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 5 }}>Event Type (optional)</div>
                <EventTypeCombobox value={triggerType} onChange={setTriggerType} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: '#334155', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 5 }}>Platform</div>
                <select value={platform} onChange={e => setPlatform(e.target.value)}
                  style={{ width: '100%', background: '#161b28', border: '1px solid #2a3548', borderRadius: 6, padding: '7px 10px', fontSize: 11, color: '#e2e8f0', outline: 'none', cursor: 'pointer' }}>
                  <option value="any">any</option>
                  <option value="docker">docker</option>
                  <option value="kubernetes">kubernetes</option>
                  <option value="linux">linux</option>
                  <option value="windows">windows</option>
                </select>
              </div>
            </div>

            {/* error */}
            {genError && (
              <div style={{ marginTop: 10, fontSize: 11, color: '#f43f5e', background: '#1a0610', border: '1px solid #3d0a1c', borderRadius: 6, padding: '8px 12px', display: 'flex', gap: 6 }}>
                <span>⚠</span><span>{genError}</span>
              </div>
            )}

            {/* footer */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
              <button onClick={() => { setShowGenerate(false); setGenDesc(''); setGenError(''); }} style={modalBtn('#1e2231', '#94a3b8')}>Cancel</button>
              <button
                onClick={doGenerate}
                disabled={genLoading || !genDesc.trim()}
                style={{
                  ...modalBtn('#7c3aed', 'white', true),
                  background: genLoading ? '#3d1f7a' : '#7c3aed',
                  opacity: !genDesc.trim() ? 0.5 : 1,
                  cursor: genLoading || !genDesc.trim() ? 'default' : 'pointer',
                  minWidth: 120,
                }}
              >
                {genLoading ? '⟳ Generating…' : '✦ Generate →'}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

// ── Live Result Panel ────────────────────────────────────────────────────────
//
// Progressive reveal: steps appear one at a time as the animation settles each
// one.  `displayedResults` holds settled steps; `runningStep` is the one
// currently animating on the graph.  The summary footer only appears when the
// full replay is done.

const TYPE_COLOR: Record<string, string> = {
  diagnostic:   '#3b82f6',
  action:       '#f59e0b',
  verification: '#10b981',
  notify:       '#8b5cf6',
};

function StepRow({ r }: { r: StepResult }) {
  const isDryRun = r.dry_run === true;
  return (
    <div className="lre-step-enter" style={{ padding: '9px 15px', borderBottom: '1px solid #1a1d26', background: isDryRun ? '#120d00' : undefined }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: r.skipped ? '#334155' : isDryRun ? '#f59e0b' : r.success ? '#10b981' : '#e85d75', flexShrink: 0 }}>
          {r.skipped ? '—' : isDryRun ? '⊘' : r.success ? '✓' : '✗'}
        </span>
        <span style={{ fontSize: 12, fontWeight: 600, color: r.skipped ? '#475569' : '#e2e8f0', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {r.name}
        </span>
        {isDryRun && (
          <span style={{ fontSize: 9, fontWeight: 700, color: '#f59e0b', background: '#1c1500', border: '1px solid #3d2800', borderRadius: 8, padding: '1px 6px', flexShrink: 0 }}>
            DRY RUN
          </span>
        )}
        <span style={{ fontSize: 9, fontWeight: 700, color: TYPE_COLOR[r.step_type] || '#7c85a0', textTransform: 'uppercase', letterSpacing: '.07em', flexShrink: 0 }}>
          {r.step_type}
        </span>
        <span style={{ fontSize: 9, color: '#334155', fontFamily: 'monospace', flexShrink: 0 }}>{r.elapsed_ms}ms</span>
      </div>

      {(r.skip_reason || (r.message && !isDryRun)) && (
        <div style={{ fontSize: 10.5, color: '#7c85a0', lineHeight: 1.5 }}>
          {r.skip_reason || r.message}
        </div>
      )}
      {isDryRun && r.structured?.resolved_args && Object.keys(r.structured.resolved_args as object).length > 0 && (
        <div style={{ marginTop: 4, background: '#1c1500', border: '1px solid #3d2800', borderRadius: 4, padding: '4px 8px' }}>
          <div style={{ fontSize: 9, color: '#78350f', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 3 }}>Resolved args</div>
          {Object.entries(r.structured.resolved_args as Record<string, unknown>).map(([k, v]) => (
            <div key={k} style={{ fontSize: 10, fontFamily: 'monospace' }}>
              <span style={{ color: '#f59e0b' }}>{k}</span>
              <span style={{ color: '#4a5068' }}> = </span>
              <span style={{ color: '#fcd34d' }}>{String(v)}</span>
            </div>
          ))}
        </div>
      )}
      {r.command && (
        <div style={{ marginTop: 4, fontSize: 10, fontFamily: 'monospace', color: '#7ee787', background: '#0a100d', border: '1px solid #1e2a3a', borderRadius: 4, padding: '3px 7px', overflowX: 'auto', whiteSpace: 'pre' }}>
          $ {r.command}
        </div>
      )}
      {r.raw_output && !isDryRun && (() => {
        const RAW_DISPLAY_LINES = 5;
        const allLines = r.raw_output.split('\n');
        const visible  = allLines.slice(0, RAW_DISPLAY_LINES).join('\n');
        const hidden   = allLines.length - RAW_DISPLAY_LINES;
        return (
          <div style={{ marginTop: 4, fontSize: 10, fontFamily: 'monospace', color: '#94a3b8', background: '#0a100d', border: '1px solid #1e2a3a', borderRadius: 4, padding: '4px 7px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {visible}
            {(hidden > 0 || r.truncated) && (
              <div style={{ marginTop: 3, color: '#475569', fontStyle: 'italic', fontFamily: 'sans-serif', fontSize: 9 }}>
                {hidden > 0 ? `… ${hidden} more line${hidden === 1 ? '' : 's'}` : '… output truncated'}
              </div>
            )}
          </div>
        );
      })()}
      {r.structured && Object.keys(r.structured).length > 0 && !isDryRun && (
        <div style={{ marginTop: 4, background: '#061a12', border: '1px solid #0d4030', borderRadius: 4, padding: '4px 8px' }}>
          {Object.entries(r.structured).map(([k, v]) => (
            <div key={k} style={{ fontSize: 10, fontFamily: 'monospace' }}>
              <span style={{ color: '#3b82f6' }}>{k}</span>
              <span style={{ color: '#4a5068' }}> = </span>
              <span style={{ color: '#10b981' }}>{String(v)}</span>
            </div>
          ))}
        </div>
      )}
      {r.error && !r.message?.includes(r.error) && (
        <div style={{ marginTop: 4, fontSize: 10, color: '#e85d75', fontFamily: 'monospace', background: '#1a0610', border: '1px solid #3d0a1c', borderRadius: 4, padding: '3px 7px' }}>
          {r.error}
        </div>
      )}
    </div>
  );
}

function RunningRow({ step }: { step: StepResult }) {
  return (
    <div className="lre-step-enter" style={{ padding: '10px 15px', borderBottom: '1px solid #1a1d26', background: '#120d00' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <span className="lre-spin" style={{ fontSize: 13, color: '#f59e0b', flexShrink: 0 }}>⟳</span>
        <span style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {step.name}
        </span>
        <span style={{ fontSize: 9, fontWeight: 700, color: TYPE_COLOR[step.step_type] || '#7c85a0', textTransform: 'uppercase', letterSpacing: '.07em', flexShrink: 0 }}>
          {step.step_type}
        </span>
      </div>
      {step.tool && (
        <div style={{ fontSize: 10, color: '#4a5068', marginTop: 3, fontFamily: 'monospace', paddingLeft: 20 }}>
          {step.tool}
        </div>
      )}
      {/* Pulsing dots */}
      <div style={{ display: 'flex', gap: 4, marginTop: 7, paddingLeft: 20, alignItems: 'flex-end', height: 10 }}>
        <span className="lre-dot" style={{ height: 7 }} />
        <span className="lre-dot" style={{ height: 7 }} />
        <span className="lre-dot" style={{ height: 7 }} />
      </div>
    </div>
  );
}

function LiveResultPanel({ result, displayedResults, runningStep, running, onClose }: {
  result:           LiveResult | null;
  displayedResults: StepResult[];
  runningStep:      StepResult | null;
  running:          boolean;
  onClose:          () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as each step settles or a new running step appears
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [displayedResults.length, runningStep]);

  const totalExpected = result?.results.length ?? 0;
  const allDone       = !running && !runningStep && result !== null && displayedResults.length === totalExpected;

  return (
    <div style={{ width: 320, background: '#0d1018', borderLeft: '1px solid #1e2a3a', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
      {/* ── Header ── */}
      <div style={{ padding: '13px 15px 11px', borderBottom: '1px solid #1e2a3a', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0', flex: 1 }}>Live Execution</div>

        {/* Step counter */}
        {(displayedResults.length > 0 || runningStep) && !allDone && (
          <span style={{ fontSize: 10, color: '#4a5068', fontFamily: 'monospace' }}>
            {displayedResults.length}/{totalExpected}
          </span>
        )}

        {/* Status badge */}
        {running && (
          <span style={{ fontSize: 10, color: '#10b981', background: '#061a12', border: '1px solid #0d4030', borderRadius: 10, padding: '2px 8px' }}>
            Contacting…
          </span>
        )}
        {!running && runningStep && (
          <span style={{ fontSize: 10, color: '#f59e0b', background: '#1c1500', border: '1px solid #3d2800', borderRadius: 10, padding: '2px 8px' }}>
            Running…
          </span>
        )}
        {allDone && (
          <span style={{ fontSize: 10, background: result!.success ? '#061a12' : '#1a0610', color: result!.success ? '#10b981' : '#e85d75', border: `1px solid ${result!.success ? '#0d4030' : '#3d0a1c'}`, borderRadius: 10, padding: '2px 8px' }}>
            {result!.success ? '✓ Complete' : `${result!.failed} failed`}
          </span>
        )}
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: 14 }}>×</button>
      </div>

      {/* ── Target info ── */}
      {result && (
        <div style={{ padding: '7px 15px', background: '#0a100d', borderBottom: '1px solid #1e2a3a', flexShrink: 0 }}>
          <div style={{ fontSize: 10, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <span><span style={{ color: '#334155' }}>target: </span><span style={{ fontFamily: 'monospace', color: '#10b981' }}>{result.target}</span></span>
            <span><span style={{ color: '#334155' }}>adapter: </span><span style={{ fontFamily: 'monospace', color: '#7c85a0' }}>{result.adapter}</span></span>
            <span><span style={{ color: '#334155' }}>time: </span><span style={{ color: '#7c85a0' }}>{result.elapsed_ms}ms</span></span>
          </div>
        </div>
      )}

      {/* ── Steps (progressive) ── */}
      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
        {running && !result && (
          <div style={{ padding: 24, color: '#4a5068', fontSize: 12, textAlign: 'center' }}>
            Contacting watcher…
          </div>
        )}

        {/* Settled steps */}
        {displayedResults.map((r, i) => <StepRow key={i} r={r} />)}

        {/* Currently running step */}
        {runningStep && <RunningRow step={runningStep} />}
      </div>

      {/* ── Summary — only when animation is fully complete ── */}
      {allDone && result && (
        <div className="lre-step-enter" style={{ padding: '10px 15px', borderTop: '1px solid #1e2a3a', background: '#0a100d', flexShrink: 0 }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: '#334155', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>Summary</div>
          <div style={{ display: 'flex', gap: 7 }}>
            <Pill label={`${result.succeeded} succeeded`} bg="#061a12" color="#10b981" />
            {result.skipped > 0 && <Pill label={`${result.skipped} skipped`}  bg="#1a1d26" color="#475569" />}
            {result.failed  > 0 && <Pill label={`${result.failed} failed`}    bg="#1a0610" color="#e85d75" />}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Sim Log Panel ─────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = { success: '#10b981', skipped: '#4a5068', failed: '#f43f5e' };
const STATUS_ICON:  Record<string, string> = { success: '✓',       skipped: '⏭',       failed: '✗' };

function SimLogPanel({ logs, done, diskPct, onClose }: { logs: SimLog[]; done: boolean; diskPct: number; onClose: () => void }) {
  const succeeded = logs.filter(l => l.status === 'success').length;
  const skipped   = logs.filter(l => l.status === 'skipped').length;
  const failed    = logs.filter(l => l.status === 'failed').length;
  return (
    <div style={{ width: 300, background: '#13161f', borderLeft: '1px solid #1e2231', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
      <div style={{ padding: '13px 15px 11px', borderBottom: '1px solid #1e2231', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0', flex: 1 }}>Execution Trace</div>
        {done  && <span style={{ fontSize: 10, color: '#10b981', background: '#0d3025', border: '1px solid #0d4030', borderRadius: 10, padding: '2px 8px' }}>✓ Complete</span>}
        {!done && <span style={{ fontSize: 10, color: '#f59e0b', background: '#3d2800', border: '1px solid #4d3500', borderRadius: 10, padding: '2px 8px' }}>⟳ Running…</span>}
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: 14 }}>×</button>
      </div>
      <div style={{ padding: '8px 15px', background: '#0d0f14', borderBottom: '1px solid #1e2231', display: 'flex', gap: 12, flexShrink: 0 }}>
        <Kv k="disk_percent" v={String(diskPct)} color={diskPct > 90 ? '#f43f5e' : '#10b981'} />
        <Kv k="branch" v={diskPct > 90 ? '> 90 (escalate)' : '≤ 90 (clear logs)'} color={diskPct > 90 ? '#f43f5e' : '#3b82f6'} />
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 0' }}>
        {logs.map((l, i) => (
          <div key={i} style={{ padding: '8px 15px', borderBottom: '1px solid #1a1d26' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3 }}>
              <span style={{ fontSize: 12, color: STATUS_COLOR[l.status], fontWeight: 700 }}>{STATUS_ICON[l.status]}</span>
              <span style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0' }}>{l.name}</span>
              <span style={{ fontSize: 10, color: '#4a5068', marginLeft: 'auto', fontFamily: 'monospace' }}>{l.type}</span>
            </div>
            <div style={{ fontSize: 10.5, color: '#7c85a0', lineHeight: 1.5 }}>{l.message}</div>
            {l.outputs && Object.keys(l.outputs).length > 0 && (
              <div style={{ marginTop: 4, background: '#0d0f14', border: '1px solid #1e2231', borderRadius: 4, padding: '3px 7px' }}>
                {Object.entries(l.outputs).map(([k, v]) => (
                  <div key={k} style={{ fontSize: 10, fontFamily: 'monospace', color: '#3b82f6' }}>{k} = <span style={{ color: '#10b981' }}>{v}</span></div>
                ))}
              </div>
            )}
          </div>
        ))}
        {!done && logs.length === 0 && <div style={{ padding: 20, color: '#4a5068', fontSize: 12, textAlign: 'center' }}>Starting…</div>}
      </div>
      {done && (
        <div style={{ padding: '10px 15px', borderTop: '1px solid #1e2231', background: '#0d0f14', flexShrink: 0 }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: '#4a5068', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 7 }}>Summary</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Pill label={`${succeeded} success`} bg="#0d3025" color="#10b981" />
            <Pill label={`${skipped} skipped`}   bg="#1e2231" color="#7c85a0" />
            {failed > 0 && <Pill label={`${failed} failed`} bg="#3d0a14" color="#f43f5e" />}
          </div>
        </div>
      )}
    </div>
  );
}

function Kv({ k, v, color = '#94a3b8' }: { k: string; v: string; color?: string }) {
  return <div style={{ fontSize: 10 }}><span style={{ color: '#4a5068' }}>{k}: </span><span style={{ fontFamily: 'monospace', color }}>{v}</span></div>;
}
function Pill({ label, bg, color }: { label: string; bg: string; color: string }) {
  return <div style={{ fontSize: 10, background: bg, color, border: `1px solid ${color}30`, borderRadius: 10, padding: '2px 8px' }}>{label}</div>;
}

// ── Style helpers ─────────────────────────────────────────────────────────────

function hdrBtn(bg: string, color: string, border = 'none'): React.CSSProperties {
  return { padding: '5px 12px', borderRadius: 6, background: bg, border, color, fontSize: 11, fontWeight: 600, cursor: 'pointer' };
}
function modalBtn(bg: string, color: string, prominent = false): React.CSSProperties {
  return { padding: prominent ? '8px 18px' : '7px 14px', borderRadius: 8, background: bg, border: 'none', color, fontSize: 12, fontWeight: prominent ? 700 : 500, cursor: 'pointer' };
}
