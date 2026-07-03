/**
 * IncidentTimeline
 *
 * Renders the Incident Lifecycle Timeline tab.
 *
 * Accepts either:
 *  - A pre-built `timeline` array (legacy path from WorkflowDetailsPhase6)
 *  - OR `stateHistory` + `currentLifecycleState` and builds the full canonical
 *    list itself (new preferred path).
 *
 * The canonical lifecycle order (from agentic_os/core/models.py LifecycleState):
 *   open → investigating → waiting_approval → approved → in_remediation
 *   → awaiting_manual → resolved → closed
 *
 * Branch states (rejected, failed) are shown in a separate section below the
 * main path — they are terminal and do not fit inline.
 */

import type { CSSProperties } from 'react'
import {
  IconCircleDot,
  IconSearch,
  IconClock,
  IconCheck,
  IconX,
  IconLoader2,
  IconCircleCheck,
  IconAlertTriangle,
  IconLock,
  IconAlertCircle,
} from './icons'

// ─── Types ───────────────────────────────────────────────────────────────────

/** Raw entry from state_history (backend) or the legacy timeline builder */
export interface LifecycleTimelineEntry {
  /** lowercase snake_case backend lifecycle state, e.g. "open", "investigating" */
  state: string
  timestamp: string
  reason?: string
  duration?: string
}

interface IncidentTimelineProps {
  /**
   * Pre-built entries.  Each entry's `state` field must be a lowercase
   * LifecycleState value (open, investigating, waiting_approval, …).
   */
  timeline: LifecycleTimelineEntry[]
  /**
   * The current lifecycle_state string from the workflow (lowercase).
   * Used to highlight the active state card.
   */
  currentState: string
}

// ─── Canonical state ordering ─────────────────────────────────────────────────

/** Happy-path states in correct order */
const MAIN_PATH: string[] = [
  'open',
  'investigating',
  'waiting_approval',
  'approved',
  'in_remediation',
  'awaiting_manual',
  'resolved',
  'closed',
]

/** Terminal branch states — shown separately below the main path */
const BRANCH_STATES: string[] = ['rejected', 'failed']

/** Human-readable descriptions for every backend LifecycleState */
const STATE_DESCRIPTIONS: Record<string, string> = {
  open:             'Incident detected and opened',
  investigating:    'Gathering diagnostics and context',
  waiting_approval: 'Pending governance / human approval',
  approved:         'Remediation approved — ready to execute',
  in_remediation:   'Executing runbook steps',
  awaiting_manual:  'Automated remediation failed; human intervention required',
  resolved:         'Condition cleared / remediation successful',
  closed:           'Incident closed',
  rejected:         'Remediation rejected by policy or reviewer',
  failed:           'Workflow failed',
  // legacy / alternate names that may appear in older state_history entries
  in_progress:      'Workflow in progress',
  executing:        'Executing remediation actions',
  monitoring:       'Verifying remediation effectiveness',
  deployed:         'Deployment completed',
  rolled_back:      'Deployment rolled back',
}

function formatStateName(state: string): string {
  return state.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

// ─── Colours ──────────────────────────────────────────────────────────────────

function stateAccent(state: string): string {
  switch (state) {
    case 'open':             return '#dc2626'  // red — alert
    case 'investigating':    return '#3b82f6'  // blue — active work
    case 'waiting_approval': return '#a855f7'  // purple — blocked
    case 'approved':         return '#10b981'  // green — positive decision
    case 'in_remediation':   return '#3b82f6'  // blue — active work
    case 'awaiting_manual':  return '#f97316'  // orange — human needed
    case 'resolved':         return '#10b981'  // green — success
    case 'closed':           return '#6b7280'  // gray — done
    case 'rejected':         return '#dc2626'  // red — negative
    case 'failed':           return '#dc2626'  // red — negative
    default:                 return '#7a8ba3'
  }
}

// ─── Icon ────────────────────────────────────────────────────────────────────

function StateIcon({ state, color }: { state: string; color: string }) {
  const p = { size: 18, style: { color } }
  switch (state) {
    case 'open':             return <IconCircleDot      {...p} />
    case 'investigating':    return <IconSearch         {...p} />
    case 'waiting_approval': return <IconClock          {...p} />
    case 'approved':         return <IconCheck          {...p} />
    case 'in_remediation':   return <IconLoader2        {...p} className="animate-spin" />
    case 'awaiting_manual':  return <IconAlertCircle    {...p} />
    case 'resolved':         return <IconCircleCheck    {...p} />
    case 'closed':           return <IconLock           {...p} />
    case 'rejected':         return <IconX              {...p} />
    case 'failed':           return <IconAlertTriangle  {...p} />
    default:                 return <IconCircleDot      {...p} />
  }
}

// ─── Shared card style ────────────────────────────────────────────────────────

const OUTER_CARD: CSSProperties = {
  backgroundColor: '#1a1f2e',
  border: '1px solid #3d4557',
  borderRadius: '12px',
  overflow: 'hidden',
}

// ─── StateCard ────────────────────────────────────────────────────────────────

interface StateCardProps {
  state: string
  index: number         // 1-based display number
  isActive: boolean
  isCompleted: boolean
  isFuture: boolean
  isBranch: boolean
  entry: LifecycleTimelineEntry | undefined
}

function StateCard({ state, index, isActive, isCompleted, isBranch, entry }: StateCardProps) {
  const accent = stateAccent(state)

  const entryBg = isActive
    ? `rgba(${
        accent === '#3b82f6' ? '59,130,246'
        : accent === '#10b981' ? '16,185,129'
        : accent === '#a855f7' ? '168,85,247'
        : accent === '#f97316' ? '249,115,22'
        : '220,38,38'
      },0.08)`
    : isCompleted
    ? 'rgba(16,185,129,0.05)'
    : '#252c3c'

  const borderColor = isActive
    ? accent
    : isCompleted
    ? 'rgba(16,185,129,0.3)'
    : '#3d4557'

  const timestamp = entry?.timestamp
  const reason    = entry?.reason
  const duration  = entry?.duration

  const formatTime = (iso: string) => {
    if (!iso) return null
    try {
      // Append 'Z' if backend returned a timezone-naive UTC string
      const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso.trim()) ? iso : iso + 'Z'
      const d = new Date(s)
      if (isNaN(d.getTime())) return iso
      return d.toLocaleString('en-US', {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      })
    } catch { return iso }
  }

  return (
    <div style={{
      backgroundColor: entryBg,
      border: `1px solid ${borderColor}`,
      borderLeft: `3px solid ${isActive ? accent : isCompleted ? '#10b981' : '#3d4557'}`,
      borderRadius: '8px',
      padding: '12px 14px',
      opacity: !isActive && !isCompleted && !isBranch ? 0.45 : 1,
      boxShadow: isActive ? `0 0 0 1px ${accent}30, 0 2px 12px ${accent}15` : undefined,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
        {/* Step badge */}
        <div style={{
          width: '28px', height: '28px', flexShrink: 0,
          borderRadius: '50%',
          border: `2px solid ${isActive ? accent : isCompleted ? '#10b981' : '#3d4557'}`,
          backgroundColor: isActive ? `${accent}20` : isCompleted ? 'rgba(16,185,129,0.15)' : '#1a1f2e',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '11px', fontWeight: 700,
          color: isActive ? accent : isCompleted ? '#10b981' : '#7a8ba3',
        }}>
          {isBranch ? '!' : index}
        </div>

        {/* Content */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <StateIcon state={state} color={isActive ? accent : isCompleted ? '#10b981' : '#7a8ba3'} />
              <div>
                <p style={{ fontSize: '12px', fontWeight: 700, color: isActive ? '#e8eef5' : isCompleted ? '#c1c7d0' : '#7a8ba3', margin: 0 }}>
                  {formatStateName(state)}
                </p>
                <p style={{ fontSize: '11px', color: '#7a8ba3', marginTop: '1px', margin: '1px 0 0' }}>
                  {STATE_DESCRIPTIONS[state] ?? state}
                </p>
              </div>
            </div>

            {/* Active badge with pulsing dot */}
            {isActive && (
              <span style={{
                flexShrink: 0,
                display: 'flex', alignItems: 'center', gap: '4px',
                fontSize: '9px', fontWeight: 700, letterSpacing: '0.06em',
                color: accent,
                backgroundColor: `${accent}15`,
                border: `1px solid ${accent}40`,
                borderRadius: '5px', padding: '3px 8px',
                whiteSpace: 'nowrap',
              }}>
                <span style={{
                  width: '6px', height: '6px', borderRadius: '50%',
                  backgroundColor: accent,
                  display: 'inline-block',
                  animation: 'pulse 2s infinite',
                }} />
                Current
              </span>
            )}

            {/* Completed checkmark */}
            {isCompleted && !isActive && (
              <span style={{ flexShrink: 0, fontSize: '9px', fontWeight: 700, color: '#10b981', letterSpacing: '0.06em' }}>
                DONE
              </span>
            )}
          </div>

          {/* Details box — only when we have something to show */}
          {(reason || timestamp) && (
            <div style={{
              marginTop: '10px',
              backgroundColor: '#0f1419',
              border: '1px solid #3d4557',
              borderRadius: '6px',
              padding: '8px 10px',
            }}>
              {reason && (
                <div style={{ marginBottom: timestamp ? '8px' : 0, paddingBottom: timestamp ? '8px' : 0, borderBottom: timestamp ? '1px solid #252c3c' : 'none' }}>
                  <p style={{ fontSize: '10px', fontWeight: 600, color: '#3b82f6', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 4px' }}>
                    Context
                  </p>
                  <p style={{ fontSize: '11px', color: '#a0aec0', lineHeight: 1.5, margin: 0 }}>{reason}</p>
                </div>
              )}
              {timestamp && (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '11px', color: '#7a8ba3' }}>Timestamp</span>
                  <span style={{ fontFamily: 'monospace', fontSize: '10px', color: '#a0aec0' }}>
                    {formatTime(timestamp)}
                  </span>
                </div>
              )}
              {duration && (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '4px' }}>
                  <span style={{ fontSize: '11px', color: '#7a8ba3' }}>Duration</span>
                  <span style={{ fontFamily: 'monospace', fontSize: '11px', fontWeight: 700, color: '#e8eef5' }}>
                    {duration}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function IncidentTimeline({ timeline, currentState }: IncidentTimelineProps) {
  // Normalise the current state to lowercase snake_case (handles legacy UPPER_SNAKE)
  const normalised = currentState?.toLowerCase().replace(/ /g, '_') ?? 'open'

  // Build a lookup: state → first matching entry in timeline
  const historyMap = new Map<string, LifecycleTimelineEntry>()
  for (const entry of timeline) {
    const key = entry.state?.toLowerCase().replace(/ /g, '_')
    if (key && !historyMap.has(key)) {
      historyMap.set(key, entry)
    }
  }

  // Determine which states have been visited (present in history or is current)
  const visitedStates = new Set<string>(historyMap.keys())
  if (normalised) visitedStates.add(normalised)

  // Determine current index in MAIN_PATH for progress calculation
  const currentMainIdx = MAIN_PATH.indexOf(normalised)

  // Branch states that were actually reached
  const reachedBranches = BRANCH_STATES.filter(s => visitedStates.has(s))

  // Determine which main-path states are "completed" vs "future"
  const isMainCompleted = (state: string) => {
    const idx = MAIN_PATH.indexOf(state)
    if (idx === -1) return false
    if (normalised === state) return false   // active state is not yet "completed"
    if (currentMainIdx === -1) {
      // currentState is a branch — all history entries are completed
      return visitedStates.has(state)
    }
    return idx < currentMainIdx
  }

  const isActive = (state: string) => state === normalised

  // Collect branch entries
  const branchEntries = reachedBranches.map(s => historyMap.get(s))

  // Progress for stats card
  const completedCount = MAIN_PATH.filter(s => isMainCompleted(s)).length
  const totalMainStates = MAIN_PATH.length
  const progressPct = currentMainIdx >= 0
    ? Math.round(((currentMainIdx + 1) / totalMainStates) * 100)
    : visitedStates.has('rejected') || visitedStates.has('failed')
    ? Math.round((completedCount / totalMainStates) * 100)
    : 0

  return (
    <div className="space-y-4">
      {/* ── Main timeline card ── */}
      <div style={OUTER_CARD}>
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #3d4557', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '10px', fontWeight: 600, color: '#a0aec0', letterSpacing: '0.07em', textTransform: 'uppercase' }}>
            Incident Lifecycle Timeline
          </span>
          <span style={{ fontSize: '10px', color: '#7a8ba3' }}>
            {formatStateName(normalised)}
          </span>
        </div>

        <div style={{ padding: '16px' }} className="space-y-3">
          {MAIN_PATH.map((state, idx) => {
            const entry     = historyMap.get(state)
            const active    = isActive(state)
            const completed = isMainCompleted(state)
            const future    = !active && !completed && !visitedStates.has(state)

            return (
              <div key={state} style={{ position: 'relative' }}>
                {/* Connector line */}
                {idx < MAIN_PATH.length - 1 && (
                  <div style={{
                    position: 'absolute',
                    left: '22px',
                    top: '46px',
                    width: '2px',
                    height: '20px',
                    backgroundColor: completed ? '#10b981' : active ? stateAccent(state) + '60' : '#3d4557',
                    zIndex: 0,
                  }} />
                )}
                <div style={{ position: 'relative', zIndex: 1 }}>
                  <StateCard
                    state={state}
                    index={idx + 1}
                    isActive={active}
                    isCompleted={completed}
                    isFuture={future}
                    isBranch={false}
                    entry={entry}
                  />
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Branch states (rejected / failed) — shown if reached ── */}
      {branchEntries.length > 0 && (
        <div style={OUTER_CARD}>
          <div style={{ padding: '10px 16px', borderBottom: '1px solid #3d4557' }}>
            <span style={{ fontSize: '10px', fontWeight: 600, color: '#dc2626', letterSpacing: '0.07em', textTransform: 'uppercase' }}>
              Terminal Branch States
            </span>
          </div>
          <div style={{ padding: '16px' }} className="space-y-3">
            {reachedBranches.map((state, idx) => (
              <StateCard
                key={state}
                state={state}
                index={idx + 1}
                isActive={isActive(state)}
                isCompleted={false}
                isFuture={false}
                isBranch={true}
                entry={historyMap.get(state)}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── Statistics card ── */}
      <div style={OUTER_CARD}>
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #3d4557' }}>
          <span style={{ fontSize: '10px', fontWeight: 600, color: '#a0aec0', letterSpacing: '0.07em', textTransform: 'uppercase' }}>
            Timeline Statistics
          </span>
        </div>
        <div style={{ padding: '14px 16px' }}>
          <div className="grid grid-cols-3 gap-3">
            {[
              {
                label: 'States Visited',
                value: visitedStates.size.toString(),
                color: '#e8eef5',
              },
              {
                label: 'Current State',
                value: formatStateName(normalised),
                color: stateAccent(normalised),
              },
              {
                label: 'Progress',
                value: `${progressPct}%`,
                color: '#10b981',
              },
            ].map(({ label, value, color }) => (
              <div key={label} style={{
                backgroundColor: '#252c3c',
                border: '1px solid #3d4557',
                borderRadius: '8px',
                padding: '10px 12px',
                textAlign: 'center',
              }}>
                <p style={{ fontSize: '10px', fontWeight: 600, color: '#7a8ba3', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: '4px', margin: '0 0 4px' }}>
                  {label}
                </p>
                <p style={{ fontSize: '14px', fontWeight: 700, color, wordBreak: 'break-word', margin: 0 }}>
                  {value}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
