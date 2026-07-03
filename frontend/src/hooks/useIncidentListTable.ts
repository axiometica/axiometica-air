import { useState, useCallback, useEffect } from 'react';
import { Workflow } from '../types';
import { listWorkflowsTransformed } from '../services/api';
import { useGlobalEvents } from './useGlobalEvents';

interface UseIncidentListTableOptions {
  initialPageSize?: number;
  darkMode?: boolean;
}

interface FilterState {
  lifecycleState?: string;
  severity?: string;
  service?: string;
  businessCriticality?: string;
  q?: string;
}

export function useIncidentListTable(options: UseIncidentListTableOptions = {}) {
  const [incidents, setIncidents] = useState<Workflow[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [currentPage, setCurrentPage] = useState(0);
  const [pageSize, setPageSize] = useState(options.initialPageSize || 10);
  const [sortBy, setSortBy] = useState('created_at');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [filters, setFilters] = useState<FilterState>({});

  // Fetch incidents from API
  const fetchIncidents = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listWorkflowsTransformed({
        limit: pageSize,
        offset: currentPage * pageSize,
        workflow_type: 'incident',
        sort_by: sortBy,
        sort_order: sortOrder,
        // Map camelCase FilterState keys → snake_case API params
        lifecycle_state: filters.lifecycleState,
        severity: filters.severity,
        service: filters.service,
        business_criticality: filters.businessCriticality,
        q: filters.q,
      });

      // Phase 7: Use transformed workflows with convenience fields
      setIncidents(data.workflows);
      setTotalCount(data.total_count);
    } catch (error) {
      console.error('Failed to fetch incidents:', error);
      setIncidents([]);
      setTotalCount(0);
    } finally {
      setLoading(false);
    }
  }, [pageSize, currentPage, sortBy, sortOrder, filters]);

  // Fetch when parameters change
  useEffect(() => {
    fetchIncidents();
  }, [fetchIncidents]);

  // Live push — merge single-incident updates in place to avoid a full list
  // refetch that would flash/unmount every card. Only created events (new row
  // in the list) or updates for incidents not currently on this page need a
  // full refetch.
  useGlobalEvents(useCallback((event) => {
    if (event.type === 'incident_updated') {
      setIncidents(prev => {
        const idx = prev.findIndex(i => i.workflow_id === event.workflow_id);
        if (idx === -1) return prev; // not on this page — no-op
        const updated = { ...prev[idx] };
        if (event.lifecycle_state !== undefined) updated.lifecycle_state = event.lifecycle_state;
        if (event.severity !== undefined) updated.severity = event.severity;
        if (event.risk_score !== undefined) updated.risk_score = event.risk_score;
        if (event.remediation_outcome !== undefined) updated.remediation_outcome = event.remediation_outcome;
        if (event.duplicate_count !== undefined) updated.duplicate_count = event.duplicate_count;
        const next = [...prev];
        next[idx] = updated;
        return next;
      });
    } else if (event.type === 'incident_created') {
      fetchIncidents();
    }
  }, [fetchIncidents]));

  // Handle column sort
  const handleSort = (field: string) => {
    if (sortBy === field) {
      // Toggle sort order
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(field);
      setSortOrder('desc'); // Default to desc for new field
    }
    setCurrentPage(0); // Reset to first page
  };

  // Handle page change
  const handlePageChange = (offset: number) => {
    setCurrentPage(Math.floor(offset / pageSize));
  };

  // Handle page size change
  const handlePageSizeChange = (newSize: number) => {
    setPageSize(newSize);
    setCurrentPage(0); // Reset to first page
  };

  // Handle filter change
  const handleFilterChange = (newFilters: FilterState) => {
    setFilters(newFilters);
    setCurrentPage(0); // Reset to first page when filters change
  };

  return {
    incidents,
    totalCount,
    loading,
    currentPage,
    pageSize,
    sortBy,
    sortOrder,
    handleSort,
    handlePageChange,
    handlePageSizeChange,
    handleFilterChange,
    refetch: fetchIncidents,
  };
}
