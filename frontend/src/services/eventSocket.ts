/**
 * GlobalEventsSocket — singleton WebSocket client for /ws/events.
 *
 * One shared connection per browser tab.  Multiple React components can
 * subscribe/unsubscribe independently without creating extra connections.
 *
 * Message types pushed by the server:
 *   incident_created      — a new incident just appeared
 *   incident_updated      — lifecycle_state / severity / risk changed
 *   approval_requested    — one or more new pending approvals arrived
 *   approval_resolved     — one or more approvals were approved or rejected
 *   monitoring_event_new  — new monitoring events with status='new' arrived
 *   storm_changed         — a storm parent's state changed (detected or resolved)
 */

export interface GlobalEvent {
  type:
    | 'incident_created'
    | 'incident_updated'
    | 'approval_requested'
    | 'approval_resolved'
    | 'monitoring_event_new'
    | 'storm_changed'
  // incident / storm events
  workflow_id?: string
  incident_number_str?: string
  lifecycle_state?: string
  severity?: string
  risk_score?: number | null
  remediation_outcome?: string
  duplicate_count?: number
  // count events
  new_count?: number
  resolved_count?: number
}

type GlobalEventCallback = (event: GlobalEvent) => void

class GlobalEventsSocket {
  private ws: WebSocket | null = null
  private subscribers = new Set<GlobalEventCallback>()
  private reconnectAttempts = 0
  private readonly maxReconnects = 12
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private intentionalClose = false
  private connectionCount = 0   // tracks how many callers asked to connect

  /** Call once per component mount (idempotent — opens only one socket). */
  connect(): void {
    this.connectionCount++
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return  // already open or opening
    this.intentionalClose = false
    this._open()
  }

  /** Call on component unmount — closes the socket when the last caller leaves. */
  disconnect(): void {
    this.connectionCount = Math.max(0, this.connectionCount - 1)
    if (this.connectionCount > 0) return   // other components still need it
    this.intentionalClose = true
    this._clearTimers()
    this.ws?.close()
    this.ws = null
    this.reconnectAttempts = 0
  }

  /** Subscribe to incoming events. Returns an unsubscribe function. */
  subscribe(cb: GlobalEventCallback): () => void {
    this.subscribers.add(cb)
    return () => this.subscribers.delete(cb)
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  // ── private ────────────────────────────────────────────────────────────────

  private _open(): void {
    try {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      this.ws = new WebSocket(`${proto}//${window.location.host}/ws/events`)

      this.ws.onopen = () => {
        this.reconnectAttempts = 0
        // keepalive ping every 25 s (server expects "ping", replies "pong")
        this.pingTimer = setInterval(() => {
          if (this.ws?.readyState === WebSocket.OPEN) this.ws.send('ping')
        }, 25_000)
      }

      this.ws.onmessage = (e: MessageEvent) => {
        if (e.data === 'pong') return
        try {
          const event = JSON.parse(e.data) as GlobalEvent
          this.subscribers.forEach(cb => {
            try { cb(event) } catch (err) {
              console.error('[GlobalEvents] subscriber error:', err)
            }
          })
        } catch {
          // ignore malformed frames
        }
      }

      this.ws.onerror = () => {
        // onerror is always followed by onclose — let onclose handle reconnect
        this.ws?.close()
      }

      this.ws.onclose = () => {
        this._clearTimers()
        if (!this.intentionalClose) this._scheduleReconnect()
      }
    } catch (err) {
      console.error('[GlobalEvents] failed to open WebSocket:', err)
      this._scheduleReconnect()
    }
  }

  private _scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnects) {
      console.warn('[GlobalEvents] max reconnect attempts reached — giving up')
      return
    }
    // Exponential backoff capped at 30 s
    const delay = Math.min(1_000 * Math.pow(1.5, this.reconnectAttempts), 30_000)
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(() => this._open(), delay)
  }

  private _clearTimers(): void {
    if (this.pingTimer)     { clearInterval(this.pingTimer);   this.pingTimer = null }
    if (this.reconnectTimer){ clearTimeout(this.reconnectTimer); this.reconnectTimer = null }
  }
}

/** Singleton — import and use anywhere in the app. */
export const globalEvents = new GlobalEventsSocket()
