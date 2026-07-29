import { useState, useEffect, useRef } from 'react'

const DS = {
  bg:      '#0d1117',
  surface: '#1a1f2e',
  raised:  '#252c3c',
  border:  '#3d4557',
  txtP:    '#e8eef5',
  txtS:    '#7a8ba3',
  txtM:    '#a0aec0',
  accent:  '#3b82f6',
}

interface DemoStep {
  label: string
  resource: string
  anomaly: string | null
  event: string
}

const STEPS: DemoStep[] = [
  {
    label: 'CPU Spike',
    resource: 'agentic_os_neo4j',
    anomaly: "yes > /dev/null &",
    event: 'infrastructure.compute.cpu_high',
  },
  {
    label: 'Syscall Storm',
    resource: 'umami',
    anomaly: "yes > /dev/null &",
    event: 'infrastructure.compute.syscall_intensity_high',
  },
  {
    label: 'Disk Filling',
    resource: 'umami_db',
    anomaly: "dd if=/dev/zero of=/tmp/fillup bs=1M count=100",
    event: 'infrastructure.storage.disk_full',
  },
  {
    label: 'Memory Pressure',
    resource: 'umami',
    anomaly: null,
    event: 'infrastructure.compute.memory_high',
  },
  {
    label: 'Service Down',
    resource: 'demo-gateway-1',
    anomaly: null,
    event: 'application.availability.service_down',
  },
  {
    label: 'CPU Spike',
    resource: 'agentic_os_flower',
    anomaly: "yes > /dev/null &",
    event: 'infrastructure.compute.cpu_high',
  },
  {
    label: 'Cache Pressure',
    resource: 'agentic_os_postgres',
    anomaly: null,
    event: 'database.cache.memory_high',
  },
]

const STEP_INTERVAL = 12000

interface Props {
  open: boolean
  onClose: () => void
}

export default function DemoProgressModal({ open, onClose }: Props) {
  const [currentStep, setCurrentStep] = useState(-1)
  const [done, setDone] = useState(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!open) return

    setCurrentStep(0)
    setDone(false)

    timerRef.current = setInterval(() => {
      setCurrentStep(prev => {
        const next = prev + 1
        if (next >= STEPS.length) {
          if (timerRef.current) clearInterval(timerRef.current)
          setDone(true)
          return prev
        }
        return next
      })
    }, STEP_INTERVAL)

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [open])

  if (!open) return null

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      right: 0,
      bottom: 0,
      width: 360,
      zIndex: 9999,
      background: DS.surface,
      borderLeft: `1px solid ${DS.border}`,
      boxShadow: '-8px 0 24px rgba(0,0,0,0.4)',
      display: 'flex',
      flexDirection: 'column',
      animation: 'slide-in-right 250ms ease-out',
    }}>
      {/* Header */}
      <div style={{
        padding: '1rem 1.25rem',
        borderBottom: `1px solid ${DS.border}`,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexShrink: 0,
      }}>
        <h3 style={{ margin: 0, color: DS.txtP, fontSize: '0.9rem', fontWeight: 600 }}>
          {done ? 'All Incidents Triggered' : 'Triggering Demo Incidents...'}
        </h3>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            border: 'none',
            color: DS.txtS,
            fontSize: '1.1rem',
            cursor: 'pointer',
            padding: '2px 6px',
            lineHeight: 1,
            borderRadius: 4,
          }}
          onMouseEnter={e => { e.currentTarget.style.color = DS.txtP; e.currentTarget.style.background = DS.raised }}
          onMouseLeave={e => { e.currentTarget.style.color = DS.txtS; e.currentTarget.style.background = 'transparent' }}
        >
          x
        </button>
      </div>

      {/* Description */}
      <div style={{ padding: '0.75rem 1.25rem 0', flexShrink: 0 }}>
        <p style={{
          margin: 0,
          color: DS.txtS,
          fontSize: '0.78rem',
          lineHeight: 1.5,
        }}>
          {done
            ? 'All 7 events submitted. The AI pipeline is processing each incident — watch the list update in real time.'
            : 'Creating anomalies on safe containers and submitting events through the 7-agent pipeline.'}
        </p>

        {/* Progress bar */}
        <div style={{
          marginTop: '0.75rem',
          height: 3,
          background: DS.raised,
          borderRadius: 2,
          overflow: 'hidden',
        }}>
          <div style={{
            height: '100%',
            width: done ? '100%' : `${((currentStep + 1) / STEPS.length) * 100}%`,
            background: done ? '#059669' : '#7c3aed',
            borderRadius: 2,
            transition: 'width 500ms ease, background 300ms ease',
          }} />
        </div>
        <div style={{
          marginTop: 4,
          fontSize: '0.7rem',
          color: DS.txtS,
          textAlign: 'right',
        }}>
          {done ? '7/7 complete' : `${currentStep + 1} of ${STEPS.length}`}
        </div>
      </div>

      {/* Steps list */}
      <div style={{
        flex: 1,
        overflow: 'auto',
        padding: '0.5rem 1.25rem 1.25rem',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {STEPS.map((step, idx) => {
            const isPast = idx < currentStep
            const isCurrent = idx === currentStep && !done
            const isPending = idx > currentStep && !done
            const isDone = isPast || done

            return (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  gap: '0.6rem',
                  padding: '0.5rem 0.6rem',
                  borderRadius: 8,
                  background: isCurrent ? DS.raised : 'transparent',
                  border: isCurrent ? `1px solid ${DS.border}` : '1px solid transparent',
                  transition: 'all 300ms ease',
                  opacity: isPending ? 0.35 : 1,
                }}
              >
                <div style={{
                  width: 20,
                  height: 20,
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '0.65rem',
                  flexShrink: 0,
                  marginTop: 1,
                  ...(isDone
                    ? { background: '#059669', color: '#fff' }
                    : isCurrent
                    ? { background: '#7c3aed', color: '#fff', animation: 'pulse-ring 1.5s ease-in-out infinite' }
                    : { background: DS.raised, color: DS.txtS, border: `1px solid ${DS.border}` }),
                }}>
                  {isDone ? '✓' : idx + 1}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'baseline',
                    gap: 6,
                  }}>
                    <span style={{
                      color: DS.txtP,
                      fontSize: '0.78rem',
                      fontWeight: isCurrent ? 600 : 400,
                    }}>
                      {step.label}
                    </span>
                    <span style={{
                      color: DS.txtS,
                      fontSize: '0.68rem',
                      fontFamily: 'monospace',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}>
                      {step.resource}
                    </span>
                  </div>

                  {(isCurrent || isDone) && (
                    <div style={{ marginTop: 3 }}>
                      {step.anomaly && (
                        <div style={{
                          fontSize: '0.68rem',
                          color: isCurrent ? '#c4b5fd' : DS.txtS,
                          fontFamily: 'monospace',
                          marginBottom: 1,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}>
                          $ {step.anomaly}
                        </div>
                      )}
                      <div style={{
                        fontSize: '0.68rem',
                        color: isCurrent ? '#93c5fd' : DS.txtS,
                        fontFamily: 'monospace',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {isCurrent ? '▶ ' : ''}{step.event}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Footer */}
      {done && (
        <div style={{
          padding: '0.75rem 1.25rem',
          borderTop: `1px solid ${DS.border}`,
          flexShrink: 0,
        }}>
          <button
            onClick={onClose}
            style={{
              width: '100%',
              padding: '0.5rem',
              borderRadius: 8,
              border: `1px solid ${DS.border}`,
              background: DS.raised,
              color: DS.txtP,
              fontSize: '0.82rem',
              fontWeight: 500,
              cursor: 'pointer',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = '#7c3aed' }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = DS.border }}
          >
            Close
          </button>
        </div>
      )}

      <style>{`
        @keyframes pulse-ring {
          0%, 100% { box-shadow: 0 0 0 0 rgba(124,58,237,0.4); }
          50% { box-shadow: 0 0 0 6px rgba(124,58,237,0); }
        }
        @keyframes slide-in-right {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
    </div>
  )
}
