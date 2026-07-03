import { useState } from 'react'
import { submitChange } from '../services/api'
import { IconClipboardList, IconAlertCircle } from './icons'
import './ChangeForm.css'

interface ChangeFormProps {
  onSubmitted: (workflowId: string) => void
  darkMode?: boolean
}

const CHANGE_TYPES = [
  {
    value:   'standard',
    label:   'Standard',
    hint:    'Low Risk · Auto-approved',
    color:   '#6ee7b7',
  },
  {
    value:   'normal',
    label:   'Normal',
    hint:    'Medium Risk · CAB Review',
    color:   '#fbbf24',
  },
  {
    value:   'emergency',
    label:   'Emergency',
    hint:    'High Risk · Urgent CAB',
    color:   '#fca5a5',
  },
]

export default function ChangeForm({ onSubmitted }: ChangeFormProps) {
  const [formData, setFormData] = useState({
    change_type:       'standard',
    description:       '',
    affected_services: '',
    rollback_plan:     '',
  })
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const set = (key: string, val: string) =>
    setFormData(prev => ({ ...prev, [key]: val }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)

    const affectedServices = formData.affected_services
      .split(',')
      .map(s => s.trim())
      .filter(s => s.length > 0)

    if (affectedServices.length === 0) {
      setError('Please enter at least one affected service')
      setLoading(false)
      return
    }

    try {
      const response = await submitChange({
        change_type:       formData.change_type as any,
        description:       formData.description,
        affected_services: affectedServices,
        rollback_plan:     formData.rollback_plan,
      })
      setFormData({ change_type: 'standard', description: '', affected_services: '', rollback_plan: '' })
      onSubmitted(response.data.workflow_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit change')
    } finally {
      setLoading(false)
    }
  }

  const activeType = CHANGE_TYPES.find(t => t.value === formData.change_type)
  const canSubmit  = !loading && !!formData.description && !!formData.rollback_plan && !!formData.affected_services

  return (
    <div className="cf-page">
      <div className="cf-card">

        {/* Header */}
        <div className="cf-header">
          <div className="cf-header-icon">
            <IconClipboardList size={20} />
          </div>
          <div>
            <h1 className="cf-title">New Change Request</h1>
            <p className="cf-subtitle">Submit a change for CAB review and controlled deployment</p>
          </div>
        </div>

        {error && (
          <div className="cf-error">
            <IconAlertCircle size={15} />
            {error}
          </div>
        )}

        <form className="cf-form" onSubmit={handleSubmit}>

          {/* Change Type — card pills */}
          <div className="cf-field">
            <span className="cf-label">Change Type <span className="cf-required">*</span></span>
            <div className="cf-type-pills">
              {CHANGE_TYPES.map(t => (
                <button
                  key={t.value}
                  type="button"
                  className={`cf-type-pill${formData.change_type === t.value ? ' active' : ''}`}
                  style={formData.change_type === t.value
                    ? { borderColor: t.color, background: `${t.color}12`, color: t.color }
                    : {}}
                  onClick={() => set('change_type', t.value)}
                >
                  <span className="cf-type-label">{t.label}</span>
                  <span className="cf-type-hint">{t.hint}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Description */}
          <div className="cf-field">
            <label className="cf-label">Description <span className="cf-required">*</span></label>
            <textarea
              className="cf-textarea"
              value={formData.description}
              onChange={e => set('description', e.target.value)}
              placeholder="What is being changed and why?"
              rows={4}
              required
            />
          </div>

          {/* Affected Services */}
          <div className="cf-field">
            <label className="cf-label">Affected Services <span className="cf-required">*</span></label>
            <input
              className="cf-input"
              type="text"
              value={formData.affected_services}
              onChange={e => set('affected_services', e.target.value)}
              placeholder="e.g., api-server, database, cache"
              required
            />
            <span className="cf-hint">Separate multiple services with commas</span>
          </div>

          {/* Rollback Plan */}
          <div className="cf-field">
            <label className="cf-label">Rollback Plan <span className="cf-required">*</span></label>
            <textarea
              className="cf-textarea"
              value={formData.rollback_plan}
              onChange={e => set('rollback_plan', e.target.value)}
              placeholder="What is the rollback procedure if something goes wrong?"
              rows={4}
              required
            />
          </div>

          <button className="cf-submit" type="submit" disabled={!canSubmit}>
            {loading ? 'Submitting…' : `Submit ${activeType?.label ?? ''} Change Request`}
          </button>

        </form>
      </div>
    </div>
  )
}
