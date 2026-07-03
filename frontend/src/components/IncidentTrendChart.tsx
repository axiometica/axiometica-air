import { useState, useEffect } from 'react'
import { getIncidentTrend, TrendPoint } from '../services/api'

export default function IncidentTrendChart() {
  const [data, setData]       = useState<TrendPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [hovered, setHovered] = useState<TrendPoint | null>(null)

  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [])

  const load = async () => {
    try {
      const res = await getIncidentTrend(7)
      setData(res.data)
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }

  if (loading && data.length === 0) {
    return <div className="metric-card h-48 skeleton-pulse mb-8" />
  }

  const maxVal  = Math.max(...data.flatMap(d => [d.created, d.resolved]), 1)
  const totalCreated  = data.reduce((s, d) => s + d.created,  0)
  const totalResolved = data.reduce((s, d) => s + d.resolved, 0)

  // MTTR line: scale based on max MTTR across the week (null days are gaps)
  const mttrValues = data.map(d => d.avg_mttr_seconds ?? null)
  const maxMttr = Math.max(...mttrValues.filter((v): v is number => v !== null), 1)
  const fmtMttr = (sec: number | null | undefined) => {
    if (!sec) return '—'
    const m = Math.round(sec / 60)
    return m >= 60 ? `${Math.round(m / 60)}h ${m % 60}m` : `${m}m`
  }

  // SVG layout
  const W          = 600
  const H          = 100
  const padL       = 6
  const padR       = 6
  const padTop     = 8
  const padBot     = 22
  const innerW     = W - padL - padR
  const innerH     = H - padTop - padBot
  const daySlot    = innerW / data.length
  const barW       = Math.min(daySlot * 0.28, 18)
  const barGap     = 3

  return (
    <div className="metric-card mb-8" style={{ animation: 'staggerFadeIn 0.4s ease-out forwards', animationDelay: '480ms', opacity: 0 }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#e8eef5' }}>7-Day Incident Trend</h3>
          <p className="text-xs" style={{ color: '#7a8ba3' }}>Incidents created vs resolved per day</p>
        </div>
        <div className="flex items-center gap-4 text-xs" style={{ color: '#7a8ba3' }}>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ backgroundColor: '#3b82f6' }} />
            <span>Created ({totalCreated})</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ backgroundColor: '#10b981' }} />
            <span>Resolved ({totalResolved})</span>
          </div>
          <div className="flex items-center gap-1.5">
            <svg width="16" height="8" style={{ display: 'inline-block' }}>
              <line x1="0" y1="4" x2="16" y2="4" stroke="#a78bfa" strokeWidth="1.5" strokeDasharray="3 2" />
            </svg>
            <span>Avg MTTR</span>
          </div>
        </div>
      </div>

      {/* Tooltip */}
      {hovered && (
        <div className="mb-2 flex items-center gap-3 text-xs px-2 py-1.5 rounded-md" style={{ backgroundColor: '#1e2535' }}>
          <span style={{ color: '#a0aec0' }}>{hovered.label}</span>
          <span style={{ color: '#3b82f6' }}>Created: <strong>{hovered.created}</strong></span>
          <span style={{ color: '#10b981' }}>Resolved: <strong>{hovered.resolved}</strong></span>
          {hovered.avg_mttr_seconds != null && (
            <span style={{ color: '#a78bfa' }}>MTTR: <strong>{fmtMttr(hovered.avg_mttr_seconds)}</strong></span>
          )}
          {hovered.created > hovered.resolved && (
            <span style={{ color: '#f59e0b' }}>+{hovered.created - hovered.resolved} backlog</span>
          )}
          {hovered.resolved > hovered.created && (
            <span style={{ color: '#10b981' }}>−{hovered.resolved - hovered.created} cleared</span>
          )}
        </div>
      )}

      {/* Chart */}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height: 120, overflow: 'visible' }}
      >
        {/* Horizontal grid lines */}
        {[0, 0.5, 1].map((frac) => (
          <line
            key={frac}
            x1={padL} y1={padTop + innerH * (1 - frac)}
            x2={W - padR} y2={padTop + innerH * (1 - frac)}
            stroke="#1e2535" strokeWidth={1}
          />
        ))}

        {data.map((day, i) => {
          const cx         = padL + i * daySlot + daySlot / 2
          const createdH   = maxVal > 0 ? (day.created  / maxVal) * innerH : 0
          const resolvedH  = maxVal > 0 ? (day.resolved / maxVal) * innerH : 0
          const bx1        = cx - barW - barGap / 2
          const bx2        = cx + barGap / 2
          const isHov      = hovered?.date === day.date
          const barAlpha   = hovered ? (isHov ? 1 : 0.4) : 1

          return (
            <g
              key={day.date}
              onMouseEnter={() => setHovered(day)}
              onMouseLeave={() => setHovered(null)}
              style={{ cursor: 'default' }}
            >
              {/* Invisible hit area */}
              <rect
                x={padL + i * daySlot} y={padTop}
                width={daySlot} height={innerH + padBot}
                fill="transparent"
              />

              {/* Created bar (blue) */}
              {day.created > 0 && (
                <rect
                  x={bx1} y={padTop + innerH - createdH}
                  width={barW} height={createdH}
                  rx={2}
                  fill={`rgba(59,130,246,${barAlpha})`}
                />
              )}

              {/* Resolved bar (green) */}
              {day.resolved > 0 && (
                <rect
                  x={bx2} y={padTop + innerH - resolvedH}
                  width={barW} height={resolvedH}
                  rx={2}
                  fill={`rgba(16,185,129,${barAlpha})`}
                />
              )}

              {/* Empty day marker */}
              {day.created === 0 && day.resolved === 0 && (
                <circle cx={cx} cy={padTop + innerH} r={2} fill="#2a3349" />
              )}

              {/* Count labels above bars (only when hovered or value > 0) */}
              {isHov && day.created > 0 && (
                <text x={bx1 + barW / 2} y={padTop + innerH - createdH - 4}
                  textAnchor="middle" fontSize={9} fill="#3b82f6" fontWeight={600}>
                  {day.created}
                </text>
              )}
              {isHov && day.resolved > 0 && (
                <text x={bx2 + barW / 2} y={padTop + innerH - resolvedH - 4}
                  textAnchor="middle" fontSize={9} fill="#10b981" fontWeight={600}>
                  {day.resolved}
                </text>
              )}

              {/* Day label */}
              <text
                x={cx} y={padTop + innerH + 14}
                textAnchor="middle" fontSize={10}
                fill={isHov ? '#e8eef5' : '#4b5563'}
                fontWeight={isHov ? 600 : 400}
              >
                {day.label}
              </text>
            </g>
          )
        })}

        {/* ── MTTR trend line (dashed purple) ── */}
        {mttrValues.some(v => v !== null) && (() => {
          const pts = data.map((day, i) => ({
            x: padL + i * daySlot + daySlot / 2,
            y: day.avg_mttr_seconds != null
              ? padTop + innerH - (day.avg_mttr_seconds / maxMttr) * innerH
              : null,
            date: day.date,
            mttr: day.avg_mttr_seconds,
          }))
          return (
            <g>
              {/* Line segments */}
              {pts.map((pt, i) => {
                if (i === 0 || pt.y === null || pts[i - 1].y === null) return null
                return (
                  <line key={`ml${i}`}
                    x1={pts[i - 1].x} y1={pts[i - 1].y!} x2={pt.x} y2={pt.y}
                    stroke="#a78bfa" strokeWidth={1.5} strokeDasharray="3 2"
                    opacity={hovered ? 0.5 : 0.85}
                  />
                )
              })}
              {/* Dots + hover label */}
              {pts.map((pt, i) => pt.y !== null && (
                <g key={`md${i}`}>
                  <circle
                    cx={pt.x} cy={pt.y} r={2.5}
                    fill="#a78bfa" stroke="#111827" strokeWidth={1.5}
                    opacity={hovered ? (hovered.date === pt.date ? 1 : 0.25) : 0.9}
                  />
                  {hovered?.date === pt.date && (
                    <text x={pt.x} y={pt.y - 6}
                      textAnchor="middle" fontSize={9} fill="#a78bfa" fontWeight={600}>
                      {fmtMttr(pt.mttr)}
                    </text>
                  )}
                </g>
              ))}
            </g>
          )
        })()}
      </svg>

      {/* Net summary */}
      <div className="flex items-center justify-end gap-4 mt-1 pt-2 border-t border-slate-700/40">
        <span className="text-xs" style={{ color: '#4b5563' }}>7-day net:</span>
        <span className="text-xs font-semibold" style={{
          color: totalResolved >= totalCreated ? '#10b981' : '#f59e0b'
        }}>
          {totalResolved >= totalCreated
            ? `↓ ${totalCreated - totalResolved === 0 ? 'balanced' : `${totalResolved - totalCreated} cleared`}`
            : `↑ +${totalCreated - totalResolved} backlog growth`}
        </span>
      </div>
    </div>
  )
}
