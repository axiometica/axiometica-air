/**
 * Compact Platform Intelligence summary card shown on the Dashboard.
 * Fetches pending recommendation count + key health metrics.
 */

import { useState, useEffect } from 'react'
import { countPendingRecommendations, getPlatformHealth } from '../services/api'
import { PlatformIntelIcon } from './IconWrappers'

interface Props {
  onNavigate?: (view: string) => void
  darkMode?: boolean   // reserved for future light-mode support; card inherits metric-card styles
}

export default function PlatformIntelCard({ onNavigate }: Props) {
  const [pending, setPending] = useState<number | null>(null)
  const [autoRate, setAutoRate] = useState<number | null>(null)
  const [fpRate, setFpRate] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        const [countRes, healthRes] = await Promise.all([
          countPendingRecommendations(),
          getPlatformHealth(30),
        ])
        setPending(countRes.data.pending)
        setAutoRate(healthRes.data.automation_rate)
        setFpRate(healthRes.data.false_positive_rate)
      } catch {
        // silently ignore — component is optional on dashboard
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) return null  // don't show a skeleton — dashboard is already busy

  const hasPending = (pending ?? 0) > 0

  const accentColor = hasPending ? '#f97316' : '#818cf8'

  return (
    <div
      className={`metric-card relative overflow-hidden${onNavigate ? ' cursor-pointer' : ''}`}
      onClick={() => onNavigate?.('platform-intelligence')}
      title="Platform Intelligence"
    >
      {/* Coloured top bar — matches WatcherCard / ApprovalsCard pattern */}
      <div
        className="absolute top-0 left-0 right-0 h-1"
        style={{ background: hasPending
          ? 'linear-gradient(to right, #f97316, #fb923c)'
          : 'linear-gradient(to right, #818cf8, #a5b4fc)' }}
      />

      <div className="relative flex items-center gap-4">
        {/* Circular icon — same size & border style as WatcherCard / ApprovalsCard */}
        <div
          className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ border: `2px solid ${accentColor}`, color: accentColor }}
        >
          <PlatformIntelIcon size={20} strokeWidth={1.75} />
        </div>

        {/* Label block */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            {hasPending && (
              <span className="w-2 h-2 rounded-full bg-warning-500 animate-metric-pulse" />
            )}
            <p className="text-sm font-semibold" style={{ color: accentColor }}>
              Platform Intelligence
            </p>
          </div>
          <p className="text-xs" style={{ color: '#7a8ba3' }}>
            {hasPending
              ? `${pending} recommendation${pending !== 1 ? 's' : ''} awaiting review`
              : 'AI-powered configuration tuning'}
          </p>
        </div>

        {/* Metrics */}
        <div className="flex items-center gap-4 flex-shrink-0">
          {pending != null && (
            <div style={{ textAlign: 'center' }}>
              <p style={{ fontSize: '20px', fontWeight: 800, color: hasPending ? '#f97316' : '#10b981', lineHeight: 1, margin: 0 }}>
                {pending}
              </p>
              <p style={{ fontSize: '9px', color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.05em', margin: 0 }}>
                pending
              </p>
            </div>
          )}
          {autoRate != null && (
            <div style={{ textAlign: 'center' }}>
              <p style={{
                fontSize: '20px', fontWeight: 800, lineHeight: 1, margin: 0,
                color: autoRate >= 0.5 ? '#10b981' : autoRate >= 0.3 ? '#f59e0b' : '#dc2626',
              }}>
                {Math.round(autoRate * 100)}%
              </p>
              <p style={{ fontSize: '9px', color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.05em', margin: 0 }}>
                automated
              </p>
            </div>
          )}
          {fpRate != null && (
            <div style={{ textAlign: 'center' }}>
              <p style={{
                fontSize: '20px', fontWeight: 800, lineHeight: 1, margin: 0,
                color: fpRate <= 0.1 ? '#10b981' : fpRate <= 0.25 ? '#f59e0b' : '#dc2626',
              }}>
                {Math.round(fpRate * 100)}%
              </p>
              <p style={{ fontSize: '9px', color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.05em', margin: 0 }}>
                false pos.
              </p>
            </div>
          )}
          {onNavigate && (
            <span style={{ color: '#4b5563', fontSize: '16px' }}>›</span>
          )}
        </div>
      </div>
    </div>
  )
}
