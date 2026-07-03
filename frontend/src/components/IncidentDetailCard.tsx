import React from 'react';
import { Workflow } from '../types';
import './IncidentDetailCard.css';

interface IncidentDetailCardProps {
  incident: Workflow;
  onClose?: () => void;
  darkMode?: boolean;
}

export const IncidentDetailCard: React.FC<IncidentDetailCardProps> = ({ incident }) => {
  const ctx          = incident.context as any;
  const sentinel     = ctx?.sentinel;
  const cmdb         = ctx?.cmdb;
  const risk         = ctx?.risk;
  const governance   = ctx?.governance;
  const alertPayload = ctx?.alert_payload;

  // Duration (created → updated or now)
  const TERMINAL = new Set(['resolved', 'closed', 'failed', 'rolled_back', 'rejected']);
  const endMs    = TERMINAL.has(incident.lifecycle_state ?? '')
    ? new Date(incident.updated_at).getTime()
    : Date.now();
  const durMins  = Math.floor((endMs - new Date(incident.created_at).getTime()) / 60000);
  const durLabel = durMins < 60 ? `${durMins}m` : `${Math.floor(durMins / 60)}h ${durMins % 60}m`;

  // Field helpers
  const anomalyType   = sentinel?.anomaly_type  || alertPayload?.type        || '—';
  const resource      = cmdb?.resource_name     || alertPayload?.resource_name || '—';
  const environment   = cmdb?.environment       || alertPayload?.environment  || '—';
  const owner         = cmdb?.resource_info?.owner || alertPayload?.owner    || '—';
  const resourceType  = cmdb?.resource_info?.type  || alertPayload?.resource_type || '—';
  const confidence    = sentinel?.confidence != null
    ? `${Math.round((sentinel.confidence <= 1 ? sentinel.confidence * 100 : sentinel.confidence))}%`
    : '—';
  const blastRadius   = risk?.blast_radius ?? '—';
  const govDecision   = governance?.decision_notes
    || (governance?.approval_required === false ? 'Auto-approved' : null)
    || '—';
  const _rawDesc      = alertPayload?.description
  const description   = (_rawDesc && !_rawDesc.startsWith('QUALIFIED:'))
    ? _rawDesc
    : alertPayload?.message
      || sentinel?.alert_payload?.message
      || null;

  return (
    <div className="idc-root">
      {/* ── Header ─────────────────────────────────────── */}
      <div className="idc-header">
        <span className="idc-header-label">Alert Details</span>
        <span className="idc-header-meta">{durLabel} elapsed</span>
      </div>

      {/* ── Key-value grid ─────────────────────────────── */}
      <div className="idc-grid">
        <div className="idc-field">
          <span className="idc-key">Anomaly Type</span>
          <span className="idc-val">{anomalyType}</span>
        </div>
        <div className="idc-field">
          <span className="idc-key">Resource</span>
          <span className="idc-val">{resource}</span>
        </div>
        <div className="idc-field">
          <span className="idc-key">Environment</span>
          <span className="idc-val">{environment}</span>
        </div>
        <div className="idc-field">
          <span className="idc-key">Confidence</span>
          <span className="idc-val">{confidence}</span>
        </div>
        <div className="idc-field">
          <span className="idc-key">Owner</span>
          <span className="idc-val">{owner}</span>
        </div>
        <div className="idc-field">
          <span className="idc-key">Resource Type</span>
          <span className="idc-val">{resourceType}</span>
        </div>
        <div className="idc-field">
          <span className="idc-key">Blast Radius</span>
          <span className="idc-val">{blastRadius}</span>
        </div>
        <div className="idc-field">
          <span className="idc-key">Governance</span>
          <span className="idc-val idc-val-governance">{govDecision}</span>
        </div>
      </div>

      {/* ── Description ────────────────────────────────── */}
      {description && (
        <div className="idc-description-section">
          <span className="idc-key">Description</span>
          <p className="idc-description">{description}</p>
        </div>
      )}
    </div>
  );
};

export default IncidentDetailCard;
