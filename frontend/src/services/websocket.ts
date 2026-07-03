export interface WorkflowUpdate {
  type: string
  workflow_id: string
  lifecycle_state?: string
  severity?: string
  risk_score?: number
  governance_decision?: string
  reasoning_trace_count?: number
  last_trace?: string
  final_state?: string
}

export type WebSocketCallback = (message: WorkflowUpdate) => void

export class WorkflowWebSocket {
  private ws: WebSocket | null = null
  private workflowId: string
  private callbacks: Set<WebSocketCallback> = new Set()
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private reconnectDelay = 1000

  constructor(workflowId: string) {
    this.workflowId = workflowId
  }

  connect(onMessage?: WebSocketCallback): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const wsUrl = `${protocol}//${window.location.host}/ws/workflows/${this.workflowId}`

        this.ws = new WebSocket(wsUrl)

        this.ws.onopen = () => {
          console.log(`WebSocket connected to ${this.workflowId}`)
          this.reconnectAttempts = 0

          if (onMessage) {
            this.subscribe(onMessage)
          }

          // Send periodic pings to keep connection alive
          this.startPingInterval()
          resolve()
        }

        this.ws.onmessage = (event) => {
          if (typeof event.data === 'string' && (event.data === 'pong' || event.data === 'ping')) return
          try {
            const message: WorkflowUpdate = JSON.parse(event.data)
            this.notifyCallbacks(message)
          } catch (e) {
            console.error('Error parsing WebSocket message:', e)
          }
        }

        this.ws.onerror = (error) => {
          console.error('WebSocket error:', error)
          reject(error)
        }

        this.ws.onclose = () => {
          console.log(`WebSocket disconnected from ${this.workflowId}`)
          this.attemptReconnect()
        }
      } catch (error) {
        reject(error)
      }
    })
  }

  subscribe(callback: WebSocketCallback): void {
    this.callbacks.add(callback)
  }

  unsubscribe(callback: WebSocketCallback): void {
    this.callbacks.delete(callback)
  }

  private notifyCallbacks(message: WorkflowUpdate): void {
    this.callbacks.forEach((callback) => {
      try {
        callback(message)
      } catch (e) {
        console.error('Error in WebSocket callback:', e)
      }
    })
  }

  private startPingInterval(): void {
    setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send('ping')
      }
    }, 30000) // Every 30 seconds
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++
      const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1)
      console.log(`Attempting to reconnect in ${delay}ms...`)

      setTimeout(() => {
        this.connect()
      }, delay)
    } else {
      console.error('Max reconnection attempts reached')
    }
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }
}
