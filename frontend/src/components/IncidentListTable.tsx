import React, { useState, useMemo } from 'react';
import ReactDOM from 'react-dom';
import { Workflow } from '../types';
import IncidentDetailCard from './IncidentDetailCard';
import { IconShield } from './icons';
import './IncidentListTable.css';

interface IncidentListTableProps {
  incidents: Workflow[];
  totalCount: number;
  loading: boolean;
  onSort: (field: string) => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (size: number) => void;
  onFilterChange: (filters: FilterState) => void;
  onApprove?: (incident: Workflow) => void;
  onViewDetails: (workflowId: string) => void;
  onRetry?: (workflowId: string) => void;
  darkMode?: boolean;
  currentPage: number;
  pageSize: number;
  sortBy: string;
  sortOrder: 'asc' | 'desc';
}

interface FilterState {
  lifecycleState?: string;
  severity?: string;
  service?: string;
  businessCriticality?: string;
}

const CRITICALITY_LABEL: Record<string, string> = {
  tier_1: 'Mission Critical',
  tier_2: 'Core Service',
  tier_3: 'Infrastructure',
};

interface HoverModal {
  title: string;
  summary: string;
  duration: string;
  top: number;
}

/** Parse a backend UTC timestamp (may lack 'Z') as UTC so local time is correct. */
const parseUTC = (s: string): Date => {
  if (!s) return new Date(NaN);
  return /Z$|[+-]\d{2}:\d{2}$/.test(s.trim()) ? new Date(s) : new Date(s + 'Z');
};

/** Compact: 04:36 · 05/17 */
const formatCompactDate = (isoString: string): string => {
  const d = parseUTC(isoString);
  const hh = d.getHours().toString().padStart(2, '0');
  const mm = d.getMinutes().toString().padStart(2, '0');
  const mo = (d.getMonth() + 1).toString().padStart(2, '0');
  const dd = d.getDate().toString().padStart(2, '0');
  return `${hh}:${mm} · ${mo}/${dd}`;
};

const formatDuration = (minutes?: number): string => {
  if (!minutes) return '—';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
};

const getSeverityBadgeClass = (severity: string | null | undefined): string => {
  switch (severity?.toLowerCase()) {
    case 'critical': return 'severity-critical';
    case 'high':     return 'severity-high';
    case 'medium':   return 'severity-medium';
    case 'low':      return 'severity-low';
    default:         return 'severity-medium';
  }
};

const getStatusBadgeClass = (status?: string): string => {
  switch (status?.toLowerCase()) {
    // Amber — decision gate
    case 'waiting_approval':  return 'status-amber';
    // Cyan — decided, about to execute
    case 'approved':          return 'status-cyan';
    // Orange — human action required (escalated, not catastrophic)
    case 'awaiting_manual':
    case 'failed':
    case 'rejected':
    case 'rolled_back':       return 'status-orange';
    // Violet — held in storm cluster
    case 'storm_hold':        return 'status-purple';
    // Emerald — terminal / done
    case 'resolved':
    case 'closed':
    case 'monitoring':
    case 'deployed':          return 'status-green';
    // Slate — Phase 1, system working (default)
    case 'open':
    case 'in_progress':
    case 'investigating':
    case 'diagnostics':
    case 'executing':
    case 'verifying':
    default:                  return 'status-slate';
  }
};

const TERMINAL_STATES = new Set(['resolved', 'closed', 'failed', 'rolled_back', 'rejected']);

/** Short status labels so the STATUS column stays compact */
const STATUS_LABEL: Record<string, string> = {
  open:             'Open',
  investigating:    'Investigating',
  waiting_approval: 'Pending',
  approved:         'Approved',
  in_progress:      'In Progress',
  executing:        'Executing',
  awaiting_manual:  'Manual',
  storm_hold:       'Storm Hold',
  resolved:         'Resolved',
  failed:           'Failed',
  rejected:         'Rejected',
  closed:           'Closed',
};
const shortStatus = (s?: string) =>
  s ? (STATUS_LABEL[s.toLowerCase()] ?? s.replace(/_/g, ' ')) : '—';

const calculateDurationMinutes = (
  createdAt: string,
  updatedAt: string,
  lifecycleState?: string,
): number => {
  const created = new Date(createdAt).getTime();
  const end =
    lifecycleState && TERMINAL_STATES.has(lifecycleState)
      ? new Date(updatedAt).getTime()
      : Date.now();
  return Math.floor((end - created) / (1000 * 60));
};

export const IncidentListTable: React.FC<IncidentListTableProps> = ({
  incidents,
  totalCount,
  loading,
  onSort,
  onPageChange,
  onPageSizeChange,
  onFilterChange,
  onApprove,
  onViewDetails,
  onRetry,
  darkMode = false,
  currentPage,
  pageSize,
  sortBy,
  sortOrder,
}) => {
  const [hoveredRowId, setHoveredRowId]   = useState<string | null>(null);
  const [expandedRowId, setExpandedRowId] = useState<string | null>(null);
  const [filters, setFilters]             = useState<FilterState>({});
  const [hoverModal, setHoverModal]       = useState<HoverModal | null>(null);
  const [mousePos, setMousePos]           = useState<{ x: number; y: number } | null>(null);

  const lifecycleStates = ['open', 'in_progress', 'investigating', 'waiting_approval', 'approved', 'executing', 'awaiting_manual', 'storm_hold', 'resolved', 'failed', 'rejected'];
  const severities      = ['critical', 'high', 'medium', 'low'];

  const services = useMemo(() => {
    const set = new Set<string>();
    incidents.forEach(i => {
      const ctx = i.context as any;
      const ap  = ctx?.alert_payload;
      if (ap?.resource_name) set.add(ap.resource_name);
      else if (i.title) {
        const m = i.title.match(/on\s+([\w\-]+)/);
        if (m) set.add(m[1]);
      }
    });
    return Array.from(set).sort();
  }, [incidents]);

  const handleFilterChange = (key: string, value: string | undefined) => {
    const next = { ...filters, [key]: value };
    setFilters(next);
    onFilterChange(next);
  };

  const totalPages = Math.ceil(totalCount / pageSize) || 1;
  const startRow   = currentPage * pageSize + 1;
  const endRow     = Math.min((currentPage + 1) * pageSize, totalCount);

  const SortIndicator = ({ field }: { field: string }) =>
    sortBy === field
      ? <span className="sort-arrow active">{sortOrder === 'asc' ? '↑' : '↓'}</span>
      : <span className="sort-arrow idle">↕</span>;

  return (
    <div className={`ilt-root ${darkMode ? 'dark' : 'light'}`}>

      {/* ── Quick Filters ─────────────────────────────── */}
      <div className="ilt-filters">
        <div className="ilt-filter-group">
          <label>Lifecycle State</label>
          <select value={filters.lifecycleState || ''} onChange={e => handleFilterChange('lifecycleState', e.target.value || undefined)}>
            <option value="">All States</option>
            <option value="active">Active (All Open)</option>
            {lifecycleStates.map(s => <option key={s} value={s}>{STATUS_LABEL[s] ?? s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>)}
          </select>
        </div>
        <div className="ilt-filter-group">
          <label>Severity</label>
          <select value={filters.severity || ''} onChange={e => handleFilterChange('severity', e.target.value || undefined)}>
            <option value="">All Severities</option>
            {severities.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="ilt-filter-group">
          <label>Service / CI</label>
          <select value={filters.service || ''} onChange={e => handleFilterChange('service', e.target.value || undefined)}>
            <option value="">All Services</option>
            {services.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="ilt-filter-group">
          <label>Business Criticality</label>
          <select value={filters.businessCriticality || ''} onChange={e => handleFilterChange('businessCriticality', e.target.value || undefined)}>
            <option value="">All Criticality</option>
            {Object.entries(CRITICALITY_LABEL).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </div>
      </div>

      {/* ── Table ─────────────────────────────────────── */}
      <div className="ilt-table-wrapper">
        <table className="ilt-table">
          <thead>
            <tr>
              <th className="col-inc"     onClick={() => onSort('incident_number')} >INC# <SortIndicator field="incident_number" /></th>
              <th className="col-ts"      onClick={() => onSort('created_at')}      >Time <SortIndicator field="created_at" /></th>
              <th className="col-status"  onClick={() => onSort('lifecycle_state')} >Status <SortIndicator field="lifecycle_state" /></th>
              <th className="col-sev"     onClick={() => onSort('severity')}        >Sev <SortIndicator field="severity" /></th>
              <th className="col-title"  onClick={() => onSort('title')}             >Title <SortIndicator field="title" /></th>
              <th className="col-ci"     onClick={() => onSort('resource_name')}    >CI / Service <SortIndicator field="resource_name" /></th>
              <th className="col-crit"                                              >Criticality</th>
              <th className="col-risk"    onClick={() => onSort('risk_score')}      >Risk <SortIndicator field="risk_score" /></th>
              <th className="col-actions"                                           ></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={9} className="ilt-empty">Loading…</td></tr>
            ) : incidents.length === 0 ? (
              <tr><td colSpan={9} className="ilt-empty">No incidents found</td></tr>
            ) : incidents.map(incident => {
              const durationMins = calculateDurationMinutes(incident.created_at, incident.updated_at, incident.lifecycle_state);
              const isExpanded   = expandedRowId === incident.workflow_id;

              const ctx          = incident.context as any;
              const alertPayload = ctx?.alert_payload;
              const sentinel     = ctx?.sentinel;

              const resourceName = alertPayload?.resource_name || sentinel?.alert_payload?.anomaly_process || 'unknown';
              const anomalyType  = sentinel?.detected_anomaly  || alertPayload?.type || 'Unknown';

              let displayTitle = 'Unknown Incident';
              if (incident.title && incident.title !== 'Unknown Incident') {
                displayTitle = incident.title;
              } else if (anomalyType && resourceName) {
                displayTitle = `${anomalyType.replace(/_/g, ' ').toUpperCase()} on ${resourceName}`;
              }

              const displayService = alertPayload?.resource_name
                || incident.title?.match(/on\s+([\w\-]+)/)?.[1]
                || '—';

              const _rawDesc = alertPayload?.description
              const displaySummary = (_rawDesc && !_rawDesc.startsWith('QUALIFIED:'))
                ? _rawDesc
                : alertPayload?.message || `Incident on ${resourceName}`;

              return (
                <React.Fragment key={incident.workflow_id}>
                  <tr
                    className={`ilt-row ${isExpanded ? 'expanded' : ''}`}
                    onMouseEnter={e => {
                      setHoveredRowId(incident.workflow_id);
                      setHoverModal({ title: displayTitle, summary: displaySummary, duration: formatDuration(durationMins), top: 0 });
                      setMousePos({ x: e.clientX, y: e.clientY });
                    }}
                    onMouseMove={e => {
                      setMousePos({ x: e.clientX, y: e.clientY });
                    }}
                    onMouseLeave={() => {
                      setHoveredRowId(null);
                      setHoverModal(null);
                      setMousePos(null);
                    }}
                    onClick={() => setExpandedRowId(isExpanded ? null : incident.workflow_id)}
                    style={{ cursor: 'pointer' }}
                  >
                    {/* INC# */}
                    <td className="col-inc">
                      <span className="ilt-inc-num">{incident.incident_number_str || '—'}</span>
                    </td>

                    {/* Timestamp */}
                    <td className="col-ts">
                      <span className="ilt-ts">{formatCompactDate(incident.created_at)}</span>
                    </td>

                    {/* Status */}
                    <td className="col-status">
                      <span className={getStatusBadgeClass(incident.lifecycle_state)} style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.4px' }}>
                        {shortStatus(incident.lifecycle_state)}
                      </span>
                      {incident.resolution_source === 'watcher_all_clear' && (
                        <span className="ilt-sub-label purple">all-clear</span>
                      )}
                      {incident.remediation_outcome === 'aborted' && (
                        <span className="ilt-sub-label amber">aborted</span>
                      )}
                    </td>

                    {/* Severity */}
                    <td className="col-sev">
                      <span className={getSeverityBadgeClass(incident.severity)} style={{ fontSize: '0.78rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                        {incident.severity || '—'}
                      </span>
                    </td>

                    {/* Title */}
                    <td className="col-title" title={displayTitle}>
                      <span className="ilt-title-text">{displayTitle}</span>
                    </td>

                    {/* CI / Service */}
                    <td className="col-ci" title={displayService}>
                      {displayService}
                    </td>

                    {/* Business Criticality */}
                    <td className="col-crit">
                      {incident.business_criticality
                        ? (CRITICALITY_LABEL[incident.business_criticality] ?? incident.business_criticality)
                        : '—'}
                    </td>

                    {/* Risk */}
                    <td className="col-risk">
                      {incident.risk_score != null ? Math.round(incident.risk_score) : '—'}
                    </td>

                    {/* Actions */}
                    <td className="col-actions" onClick={e => e.stopPropagation()}>
                      {hoveredRowId === incident.workflow_id && (
                        <div className="ilt-actions">
                          {incident.lifecycle_state === 'waiting_approval' && onApprove && (
                            <button className="ilt-btn-approve" onClick={() => onApprove(incident)} title="Review approval">
                              <IconShield size={12} /> Review
                            </button>
                          )}
                          <button className="ilt-btn-details" onClick={() => onViewDetails(incident.workflow_id)}>
                            Details
                          </button>
                          {(incident.lifecycle_state === 'failed' || incident.lifecycle_state === 'rejected') && onRetry && (
                            <button className="ilt-btn-retry" onClick={() => onRetry(incident.workflow_id)}>
                              Retry
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>

                  {isExpanded && (
                    <tr className="ilt-expanded-row">
                      <td colSpan={9} className="ilt-expanded-cell">
                        <IncidentDetailCard incident={incident} darkMode={darkMode} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* ── Pagination ────────────────────────────────── */}
      <div className="ilt-pagination">
        <span className="ilt-pg-info">
          {totalCount === 0 ? 'No incidents' : `${startRow}–${endRow} of ${totalCount}`}
        </span>
        <div className="ilt-pg-controls">
          <label className="ilt-pg-size">
            Per page
            <select value={pageSize} onChange={e => onPageSizeChange(parseInt(e.target.value))}>
              <option value={10}>10</option>
              <option value={20}>20</option>
              <option value={50}>50</option>
            </select>
          </label>
          <div className="ilt-pg-btns">
            <button onClick={() => onPageChange(0)}                                             disabled={currentPage === 0}>First</button>
            <button onClick={() => onPageChange(Math.max(0, currentPage - 1) * pageSize)}      disabled={currentPage === 0}>Prev</button>
            <span className="ilt-pg-indicator">Page {currentPage + 1} / {totalPages}</span>
            <button onClick={() => onPageChange((currentPage + 1) * pageSize)}                 disabled={currentPage >= totalPages - 1}>Next</button>
            <button onClick={() => onPageChange((totalPages - 1) * pageSize)}                  disabled={currentPage >= totalPages - 1}>Last</button>
          </div>
        </div>
      </div>

      {/* ── Summary hover modal ───────────────────────── */}
      {hoverModal && mousePos && ReactDOM.createPortal(
        <div
          className="ilt-summary-modal"
          style={{
            left: Math.min(mousePos.x + 18, window.innerWidth  - 344) + 'px',
            top:  Math.min(mousePos.y + 14, window.innerHeight - 224) + 'px',
          }}
        >
          <div className="ilt-sm-header">
            <span className="ilt-sm-title">{hoverModal.title}</span>
            {hoverModal.duration !== '—' && (
              <span className="ilt-sm-duration">{hoverModal.duration}</span>
            )}
          </div>
          <p className="ilt-sm-body">{hoverModal.summary}</p>
        </div>,
        document.body
      )}
    </div>
  );
};

export default IncidentListTable;
