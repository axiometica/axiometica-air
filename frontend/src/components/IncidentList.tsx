import { useState, useCallback, useRef } from 'react'
import IncidentCard from './IncidentCard'
import { IncidentListTable } from './IncidentListTable'
import ApprovalModal from './ApprovalModal'
import { useIncidentListTable } from '../hooks/useIncidentListTable'
import { useIncidentActions } from '../hooks/useIncidentActions'
import { Workflow } from '../types'
import {
  IconArrowLeft,
  IconRefresh,
  IconAlertCircle,
  IconSearch,
} from './icons'

interface IncidentListProps {
  onViewWorkflow: (workflowId: string) => void
  onBack: () => void
  darkMode?: boolean
}

export default function IncidentList({ onViewWorkflow, onBack, darkMode = true }: IncidentListProps) {
  // State
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid')
  const [approvalTarget, setApprovalTarget] = useState<Workflow | null>(null)
  const [selectedFilters, setSelectedFilters] = useState<{
    lifecycleState?: string
    severity?: string
    service?: string
    businessCriticality?: string
    q?: string
  }>({
    lifecycleState: undefined,
    severity: undefined,
    service: undefined,
    businessCriticality: undefined,
    q: undefined,
  })
  const [searchInput, setSearchInput] = useState('')
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Hooks
  const {
    incidents,
    loading,
    totalCount,
    currentPage,
    pageSize,
    handlePageChange,
    handlePageSizeChange,
    handleFilterChange,
    refetch,
  } = useIncidentListTable({ initialPageSize: 12 })

  const { approve, diagnosticsOnly, reject, loading: actionLoading, error: actionError, clearError } = useIncidentActions()

  // Handlers — stable refs so IncidentCard.memo comparison doesn't fail on every render
  const handleOpenApproval = useCallback((incident: Workflow) => {
    clearError()
    setApprovalTarget(incident)
  }, [clearError])

  const handleApprovalSubmit = async (
    action: 'approve' | 'diagnostics' | 'reject',
    notes: string,
  ): Promise<boolean> => {
    if (!approvalTarget) return false
    const id = approvalTarget.workflow_id
    let success = false
    if (action === 'approve') success = await approve(id, notes)
    else if (action === 'diagnostics') success = await diagnosticsOnly(id, notes)
    else success = await reject(id, notes)
    if (success) {
      setApprovalTarget(null)
      refetch()
    }
    return success
  }

  const handleViewDetails = useCallback((workflowId: string) => {
    onViewWorkflow(workflowId)
  }, [onViewWorkflow])

  const handleRefresh = async () => {
    refetch()
  }

  // Update filters when dropdown changes
  const handleLifecycleFilterChange = (value: string) => {
    const newFilters = {
      ...selectedFilters,
      lifecycleState: value || undefined,
    }
    setSelectedFilters(newFilters)
    handleFilterChange(newFilters)
  }

  const handleSeverityFilterChange = (value: string) => {
    const newFilters = {
      ...selectedFilters,
      severity: value || undefined,
    }
    setSelectedFilters(newFilters)
    handleFilterChange(newFilters)
  }

  const handleBusinessCriticalityFilterChange = (value: string) => {
    const newFilters = {
      ...selectedFilters,
      businessCriticality: value || undefined,
    }
    setSelectedFilters(newFilters)
    handleFilterChange(newFilters)
  }

  // Search updates the input immediately (so typing feels responsive) but
  // debounces the actual filter/refetch — searching across all incidents,
  // not just the current page, means every keystroke would otherwise fire
  // a new paginated query.
  const handleSearchChange = (value: string) => {
    setSearchInput(value)
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current)
    searchDebounceRef.current = setTimeout(() => {
      const newFilters = {
        ...selectedFilters,
        q: value.trim() || undefined,
      }
      setSelectedFilters(newFilters)
      handleFilterChange(newFilters)
    }, 350)
  }

  // Skeleton card for loading state
  const SkeletonCard = () => (
    <div
      style={{
        height: '300px',
        backgroundColor: '#1a1f2e',
        borderRadius: '10px',
        border: '1px solid #3d4557',
        animation: 'pulse 2s infinite',
      }}
    />
  )

  // Render
  return (
    <div className="page-transition-enter" style={{ padding: '2rem' }}>
      {/* Header Section */}
      <div style={{ marginBottom: '2rem' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '1.5rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <button
              onClick={onBack}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.5rem 1rem',
                backgroundColor: 'transparent',
                color: '#a0aec0',
                border: '1px solid #3d4557',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '0.875rem',
                fontWeight: 500,
                transition: 'all 150ms ease',
              }}
              onMouseEnter={(e) => {
                const el = e.currentTarget
                el.style.color = '#e8eef5'
                el.style.borderColor = '#a0aec0'
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget
                el.style.color = '#a0aec0'
                el.style.borderColor = '#3d4557'
              }}
              title="Back to dashboard"
            >
              <IconArrowLeft size={18} strokeWidth={2} />
              <span>Back</span>
            </button>
          </div>

          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button
              onClick={handleRefresh}
              disabled={loading}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.5rem 1rem',
                backgroundColor: '#3b82f6',
                color: '#e8eef5',
                border: '1px solid #3b82f6',
                borderRadius: '6px',
                cursor: loading ? 'not-allowed' : 'pointer',
                fontSize: '0.875rem',
                fontWeight: 500,
                transition: 'all 150ms ease',
                opacity: loading ? 0.6 : 1,
              }}
              onMouseEnter={(e) => {
                if (!loading) {
                  const el = e.currentTarget
                  el.style.backgroundColor = '#2563eb'
                  el.style.borderColor = '#2563eb'
                }
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget
                el.style.backgroundColor = '#3b82f6'
                el.style.borderColor = '#3b82f6'
              }}
              title="Refresh incidents"
            >
              <IconRefresh size={16} />
              Refresh
            </button>

            {/* View Mode Toggle */}
            <div style={{ display: 'flex', gap: '0.5rem', marginLeft: '1rem' }}>
              <button
                onClick={() => setViewMode('grid')}
                style={{
                  padding: '0.5rem 1rem',
                  backgroundColor: viewMode === 'grid' ? '#3b82f6' : 'transparent',
                  color: viewMode === 'grid' ? '#e8eef5' : '#a0aec0',
                  border: `1px solid ${viewMode === 'grid' ? '#3b82f6' : '#3d4557'}`,
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '0.875rem',
                  fontWeight: 500,
                  transition: 'all 150ms ease',
                }}
                onMouseEnter={(e) => {
                  if (viewMode !== 'grid') {
                    const el = e.currentTarget
                    el.style.borderColor = '#a0aec0'
                    el.style.color = '#e8eef5'
                  }
                }}
                onMouseLeave={(e) => {
                  if (viewMode !== 'grid') {
                    const el = e.currentTarget
                    el.style.borderColor = '#3d4557'
                    el.style.color = '#a0aec0'
                  }
                }}
              >
                Grid
              </button>
              <button
                onClick={() => setViewMode('table')}
                style={{
                  padding: '0.5rem 1rem',
                  backgroundColor: viewMode === 'table' ? '#3b82f6' : 'transparent',
                  color: viewMode === 'table' ? '#e8eef5' : '#a0aec0',
                  border: `1px solid ${viewMode === 'table' ? '#3b82f6' : '#3d4557'}`,
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '0.875rem',
                  fontWeight: 500,
                  transition: 'all 150ms ease',
                }}
                onMouseEnter={(e) => {
                  if (viewMode !== 'table') {
                    const el = e.currentTarget
                    el.style.borderColor = '#a0aec0'
                    el.style.color = '#e8eef5'
                  }
                }}
                onMouseLeave={(e) => {
                  if (viewMode !== 'table') {
                    const el = e.currentTarget
                    el.style.borderColor = '#3d4557'
                    el.style.color = '#a0aec0'
                  }
                }}
              >
                Table
              </button>
            </div>
          </div>
        </div>

        {/* Title and Description */}
        <h1 style={{ fontSize: '2rem', fontWeight: 700, color: '#e8eef5', margin: 0, marginBottom: '0.5rem' }}>
          Incidents
        </h1>
        <p style={{ color: '#a0aec0', fontSize: '0.875rem', margin: 0 }}>
          Manage and track all incidents in your system
        </p>
      </div>

      {/* Quick Filters */}
      <div
        style={{
          display: 'flex',
          gap: '1rem',
          marginBottom: '2rem',
          padding: '1rem',
          backgroundColor: '#1a1f2e',
          borderRadius: '10px',
          border: '1px solid #3d4557',
          flexWrap: 'wrap',
          alignItems: 'center',
        }}
      >
        <div style={{ position: 'relative', flex: '1 1 260px', minWidth: '220px', maxWidth: '360px' }}>
          <IconSearch
            size={16}
            style={{
              position: 'absolute',
              left: '0.75rem',
              top: '50%',
              transform: 'translateY(-50%)',
              color: '#6b7280',
              pointerEvents: 'none',
            }}
          />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder="Search incidents (title, summary, INC#)..."
            style={{
              width: '100%',
              padding: '0.5rem 1rem 0.5rem 2.25rem',
              backgroundColor: '#252c3c',
              color: '#e8eef5',
              border: '1px solid #3d4557',
              borderRadius: '6px',
              fontSize: '0.875rem',
              fontWeight: 500,
              transition: 'all 150ms ease',
              outline: 'none',
            }}
            onFocus={(e) => { e.currentTarget.style.borderColor = '#3b82f6' }}
            onBlur={(e) => { e.currentTarget.style.borderColor = '#3d4557' }}
          />
        </div>

        <select
          value={selectedFilters.lifecycleState || ''}
          onChange={(e) => handleLifecycleFilterChange(e.target.value)}
          style={{
            padding: '0.5rem 1rem',
            backgroundColor: '#252c3c',
            color: '#e8eef5',
            border: '1px solid #3d4557',
            borderRadius: '6px',
            cursor: 'pointer',
            fontSize: '0.875rem',
            fontWeight: 500,
            transition: 'all 150ms ease',
          }}
          onMouseEnter={(e) => {
            const el = e.currentTarget
            el.style.borderColor = '#a0aec0'
            el.style.backgroundColor = '#2d3447'
          }}
          onMouseLeave={(e) => {
            const el = e.currentTarget
            el.style.borderColor = '#3d4557'
            el.style.backgroundColor = '#252c3c'
          }}
        >
          <option value="">All States</option>
          <option value="active">Active (All Open)</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="investigating">Investigating</option>
          <option value="waiting_approval">Waiting Approval</option>
          <option value="approved">Approved</option>
          <option value="executing">Executing</option>
          <option value="awaiting_manual">Awaiting Manual</option>
          <option value="storm_hold">Storm Hold</option>
          <option value="resolved">Resolved</option>
          <option value="failed">Failed</option>
          <option value="rejected">Rejected</option>
        </select>

        <select
          value={selectedFilters.severity || ''}
          onChange={(e) => handleSeverityFilterChange(e.target.value)}
          style={{
            padding: '0.5rem 1rem',
            backgroundColor: '#252c3c',
            color: '#e8eef5',
            border: '1px solid #3d4557',
            borderRadius: '6px',
            cursor: 'pointer',
            fontSize: '0.875rem',
            fontWeight: 500,
            transition: 'all 150ms ease',
          }}
          onMouseEnter={(e) => {
            const el = e.currentTarget
            el.style.borderColor = '#a0aec0'
            el.style.backgroundColor = '#2d3447'
          }}
          onMouseLeave={(e) => {
            const el = e.currentTarget
            el.style.borderColor = '#3d4557'
            el.style.backgroundColor = '#252c3c'
          }}
        >
          <option value="">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>

        {/* Business criticality — distinct from severity. Severity already factors this
            in for triage ordering; managers want to slice by "what does this affect"
            independently of how each incident's overall score shook out. */}
        <select
          value={selectedFilters.businessCriticality || ''}
          onChange={(e) => handleBusinessCriticalityFilterChange(e.target.value)}
          style={{
            padding: '0.5rem 1rem',
            backgroundColor: '#252c3c',
            color: '#e8eef5',
            border: '1px solid #3d4557',
            borderRadius: '6px',
            cursor: 'pointer',
            fontSize: '0.875rem',
            fontWeight: 500,
            transition: 'all 150ms ease',
          }}
          onMouseEnter={(e) => {
            const el = e.currentTarget
            el.style.borderColor = '#a0aec0'
            el.style.backgroundColor = '#2d3447'
          }}
          onMouseLeave={(e) => {
            const el = e.currentTarget
            el.style.borderColor = '#3d4557'
            el.style.backgroundColor = '#252c3c'
          }}
        >
          <option value="">All Business Criticality</option>
          <option value="tier_1">Mission Critical</option>
          <option value="tier_2">Core Service</option>
          <option value="tier_3">Infrastructure</option>
        </select>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <IconAlertCircle size={16} style={{ color: '#a0aec0' }} />
          <span style={{ color: '#a0aec0', fontSize: '0.875rem', fontWeight: 500 }}>
            {totalCount} total incidents
          </span>
        </div>
      </div>

      {/* Grid View - Main Content */}
      {viewMode === 'grid' && (
        <>
          {/* Loading State */}
          {loading && incidents.length === 0 && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))',
                gap: '1.5rem',
                marginBottom: '2rem',
              }}
            >
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          )}

          {/* Incident Cards Grid */}
          {!loading && incidents.length > 0 && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))',
                gap: '1.5rem',
                marginBottom: '2rem',
              }}
            >
              {incidents.map((incident) => (
                <IncidentCard
                  key={incident.workflow_id || incident.id}
                  incident={incident}
                  onClick={handleViewDetails}
                  onApprove={handleOpenApproval}
                  onDetails={handleViewDetails}
                  darkMode={darkMode}
                />
              ))}
            </div>
          )}

          {/* Empty State */}
          {!loading && incidents.length === 0 && (
            <div
              style={{
                padding: '3rem 2rem',
                textAlign: 'center',
                backgroundColor: '#1a1f2e',
                borderRadius: '10px',
                border: '1px solid #3d4557',
                marginBottom: '2rem',
              }}
            >
              <IconAlertCircle size={48} style={{ color: '#6b7280', margin: '0 auto 1rem' }} />
              <p style={{ color: '#a0aec0', marginBottom: '1rem', fontSize: '0.875rem' }}>
                No incidents found matching your filters
              </p>
              <button
                onClick={handleRefresh}
                style={{
                  padding: '0.75rem 1.5rem',
                  backgroundColor: '#3b82f6',
                  color: '#e8eef5',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  transition: 'all 150ms ease',
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget
                  el.style.backgroundColor = '#2563eb'
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget
                  el.style.backgroundColor = '#3b82f6'
                }}
              >
                Refresh
              </button>
            </div>
          )}
        </>
      )}

      {/* Table View */}
      {viewMode === 'table' && (
        <IncidentListTable
          incidents={incidents}
          totalCount={totalCount}
          loading={loading}
          onSort={() => {}}
          onPageChange={handlePageChange}
          onPageSizeChange={handlePageSizeChange}
          onFilterChange={handleFilterChange}
          onApprove={handleOpenApproval}
          onViewDetails={handleViewDetails}
          darkMode={darkMode}
          currentPage={currentPage}
          pageSize={pageSize}
          sortBy="created_at"
          sortOrder="desc"
        />
      )}

      {/* Pagination Controls */}
      {!loading && incidents.length > 0 && totalCount > pageSize && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '1.5rem',
            backgroundColor: '#1a1f2e',
            borderRadius: '10px',
            border: '1px solid #3d4557',
            flexWrap: 'wrap',
            gap: '1rem',
          }}
        >
          {/* Items Per Page Selector */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <label style={{ color: '#a0aec0', fontSize: '0.875rem', fontWeight: 500 }}>
              Items per page:
            </label>
            <select
              value={pageSize}
              onChange={(e) => handlePageSizeChange(parseInt(e.target.value))}
              style={{
                padding: '0.5rem 0.75rem',
                backgroundColor: '#252c3c',
                color: '#e8eef5',
                border: '1px solid #3d4557',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '0.875rem',
                fontWeight: 500,
                transition: 'all 150ms ease',
              }}
              onMouseEnter={(e) => {
                const el = e.currentTarget
                el.style.borderColor = '#a0aec0'
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget
                el.style.borderColor = '#3d4557'
              }}
            >
              <option value={10}>10</option>
              <option value={20}>20</option>
              <option value={50}>50</option>
            </select>
          </div>

          {/* Page Info */}
          <div style={{ color: '#a0aec0', fontSize: '0.875rem', fontWeight: 500 }}>
            Page {currentPage + 1} of {Math.ceil(totalCount / pageSize)}
          </div>

          {/* Pagination Buttons */}
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button
              onClick={() => handlePageChange(Math.max(0, currentPage - 1) * pageSize)}
              disabled={currentPage === 0}
              style={{
                padding: '0.5rem 1rem',
                backgroundColor: currentPage === 0 ? '#3d4557' : '#3b82f6',
                color: '#e8eef5',
                border: 'none',
                borderRadius: '6px',
                cursor: currentPage === 0 ? 'not-allowed' : 'pointer',
                fontSize: '0.875rem',
                fontWeight: 600,
                transition: 'all 150ms ease',
                opacity: currentPage === 0 ? 0.5 : 1,
              }}
              onMouseEnter={(e) => {
                if (currentPage !== 0) {
                  const el = e.currentTarget
                  el.style.backgroundColor = '#2563eb'
                }
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget
                el.style.backgroundColor = currentPage === 0 ? '#3d4557' : '#3b82f6'
              }}
            >
              Previous
            </button>
            <button
              onClick={() => handlePageChange((currentPage + 1) * pageSize)}
              disabled={currentPage >= Math.ceil(totalCount / pageSize) - 1}
              style={{
                padding: '0.5rem 1rem',
                backgroundColor:
                  currentPage >= Math.ceil(totalCount / pageSize) - 1 ? '#3d4557' : '#3b82f6',
                color: '#e8eef5',
                border: 'none',
                borderRadius: '6px',
                cursor: currentPage >= Math.ceil(totalCount / pageSize) - 1 ? 'not-allowed' : 'pointer',
                fontSize: '0.875rem',
                fontWeight: 600,
                transition: 'all 150ms ease',
                opacity: currentPage >= Math.ceil(totalCount / pageSize) - 1 ? 0.5 : 1,
              }}
              onMouseEnter={(e) => {
                if (currentPage < Math.ceil(totalCount / pageSize) - 1) {
                  const el = e.currentTarget
                  el.style.backgroundColor = '#2563eb'
                }
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget
                el.style.backgroundColor =
                  currentPage >= Math.ceil(totalCount / pageSize) - 1 ? '#3d4557' : '#3b82f6'
              }}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* ── Approval Modal ── */}
      {approvalTarget && (
        <ApprovalModal
          incident={approvalTarget}
          loading={actionLoading}
          error={actionError}
          onApprove={(notes) => handleApprovalSubmit('approve', notes)}
          onDiagnosticsOnly={(notes) => handleApprovalSubmit('diagnostics', notes)}
          onReject={(notes) => handleApprovalSubmit('reject', notes)}
          onClose={() => { setApprovalTarget(null); clearError() }}
        />
      )}
    </div>
  )
}
