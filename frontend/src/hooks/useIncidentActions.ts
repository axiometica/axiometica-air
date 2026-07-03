import { useState, useCallback } from 'react'
import { NoteEntry } from '../types'
import { getToken } from './useCurrentUser'

/**
 * useIncidentActions Hook
 *
 * Manages incident approval, rejection, escalation, manual close, notes,
 * and remediation retry actions.  Handles API calls and loading/error states.
 */
export const useIncidentActions = () => {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Internal: submit any approval decision via the workflow-id-based endpoint
  const submitDecision = useCallback(
    async (workflowId: string, decision: string, notes: string): Promise<boolean> => {
      setLoading(true)
      setError(null)
      try {
        const response = await fetch(`/api/approvals/by-workflow/${workflowId}/decide`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
          body: JSON.stringify({ decision, notes, decided_by: 'operator' }),
        })
        if (!response.ok) {
          const data = await response.json().catch(() => ({}))
          throw new Error(data.detail || `Approval decision failed (${response.status})`)
        }
        setLoading(false)
        return true
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error')
        setLoading(false)
        return false
      }
    },
    []
  )

  const approve = useCallback(
    (workflowId: string, notes = '') => submitDecision(workflowId, 'approved', notes),
    [submitDecision]
  )

  const diagnosticsOnly = useCallback(
    (workflowId: string, notes = '') => submitDecision(workflowId, 'diagnostics_only', notes),
    [submitDecision]
  )

  const reject = useCallback(
    async (workflowId: string, reason: string): Promise<boolean> => {
      if (!reason.trim()) {
        setError('Rejection reason is required')
        return false
      }
      return submitDecision(workflowId, 'rejected', reason)
    },
    [submitDecision]
  )

  // Escalate incident
  const escalate = useCallback(
    async (workflowId: string, escalationLevel: string): Promise<boolean> => {
      setLoading(true)
      setError(null)

      try {
        const response = await fetch(`/api/workflows/${workflowId}/escalate`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${getToken()}`,
          },
          body: JSON.stringify({
            escalation_level: escalationLevel,
          }),
        })

        if (!response.ok) {
          throw new Error('Failed to escalate incident')
        }

        setLoading(false)
        return true
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : 'Unknown error'
        setError(errorMsg)
        setLoading(false)
        return false
      }
    },
    []
  )

  // Manually close an incident (post-diagnostics or operator-driven)
  const close = useCallback(
    async (
      workflowId: string,
      summary: string,
      stepsTaken: string,
      outcome: string,
      resolutionCategory?: string,
    ): Promise<boolean> => {
      if (!summary.trim() || !stepsTaken.trim()) {
        setError('Close summary and steps taken are required')
        return false
      }

      setLoading(true)
      setError(null)

      try {
        const response = await fetch(`/api/workflows/${workflowId}/close`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
          body: JSON.stringify({
            summary,
            steps_taken: stepsTaken,
            outcome,
            resolution_category: resolutionCategory ?? null,
          }),
        })

        if (!response.ok) {
          const data = await response.json().catch(() => ({}))
          throw new Error(data.detail || 'Failed to close incident')
        }

        setLoading(false)
        return true
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : 'Unknown error'
        setError(errorMsg)
        setLoading(false)
        return false
      }
    },
    []
  )

  // Add a work-log note to an incident
  const addNote = useCallback(
    async (
      workflowId: string,
      body: string,
      noteType: 'note' | 'action' | 'escalation' | 'system' = 'note',
      author = 'operator',
    ): Promise<NoteEntry | null> => {
      if (!body.trim()) {
        setError('Note body cannot be empty')
        return null
      }

      setLoading(true)
      setError(null)

      try {
        const response = await fetch(`/api/workflows/${workflowId}/notes`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
          body: JSON.stringify({ body: body.trim(), note_type: noteType, author }),
        })

        if (!response.ok) {
          const data = await response.json().catch(() => ({}))
          throw new Error(data.detail || 'Failed to add note')
        }

        const note: NoteEntry = await response.json()
        setLoading(false)
        return note
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error')
        setLoading(false)
        return null
      }
    },
    []
  )

  // Fetch all work-log notes for an incident
  const fetchNotes = useCallback(
    async (workflowId: string): Promise<NoteEntry[]> => {
      try {
        const response = await fetch(`/api/workflows/${workflowId}/notes`, {
          headers: { Authorization: `Bearer ${getToken()}` },
        })
        if (!response.ok) return []
        return (await response.json()) as NoteEntry[]
      } catch {
        return []
      }
    },
    []
  )

  // Re-queue automated remediation for an awaiting_manual incident
  const retryRemediation = useCallback(
    async (workflowId: string, reason?: string): Promise<boolean> => {
      setLoading(true)
      setError(null)

      try {
        const response = await fetch(`/api/workflows/${workflowId}/retry`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
          body: JSON.stringify({ reason: reason || 'Operator requested re-attempt' }),
        })

        if (!response.ok) {
          const data = await response.json().catch(() => ({}))
          throw new Error(data.detail || 'Failed to retry remediation')
        }

        setLoading(false)
        return true
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error')
        setLoading(false)
        return false
      }
    },
    []
  )

  // Clear error
  const clearError = useCallback(() => {
    setError(null)
  }, [])

  return {
    loading,
    error,
    approve,
    diagnosticsOnly,
    reject,
    escalate,
    close,
    addNote,
    fetchNotes,
    retryRemediation,
    clearError,
  }
}
