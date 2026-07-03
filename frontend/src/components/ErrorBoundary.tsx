import { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary] Uncaught render error:', error, info)
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div style={{
          minHeight: '200px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '12px',
          padding: '32px',
          borderRadius: '12px',
          border: '1px solid #3d4557',
          backgroundColor: '#1a1f2e',
        }}>
          <span style={{ fontSize: '24px' }}>⚠</span>
          <p style={{ fontSize: '14px', fontWeight: 600, color: '#e8eef5', margin: 0 }}>
            Something went wrong
          </p>
          <p style={{ fontSize: '12px', color: '#7a8ba3', margin: 0, fontFamily: 'monospace', maxWidth: '500px', textAlign: 'center' }}>
            {this.state.error?.message || 'An unexpected error occurred'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              marginTop: '8px',
              padding: '6px 16px',
              fontSize: '12px',
              borderRadius: '6px',
              border: '1px solid #3d4557',
              backgroundColor: '#252c3c',
              color: '#a0aec0',
              cursor: 'pointer',
            }}
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
