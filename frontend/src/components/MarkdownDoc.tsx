import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

interface MarkdownDocProps {
  content: string
}

const components: Components = {
  h1: ({ children }) => (
    <h1 style={{
      fontSize: '1.1rem', fontWeight: 700, color: '#e2e8f0',
      marginBottom: '0.25rem', marginTop: 0,
    }}>{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 style={{
      fontSize: '0.85rem', fontWeight: 700, color: '#a5b4fc',
      marginTop: '1.75rem', marginBottom: '0.5rem',
      textTransform: 'uppercase', letterSpacing: '0.06em',
      borderBottom: '1px solid rgba(165,180,252,0.15)',
      paddingBottom: '0.35rem',
    }}>{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 style={{
      fontSize: '0.875rem', fontWeight: 600, color: '#cbd5e1',
      marginTop: '1.25rem', marginBottom: '0.4rem',
    }}>{children}</h3>
  ),
  p: ({ children }) => (
    <p style={{
      fontSize: '0.825rem', color: '#94a3b8', lineHeight: 1.7,
      marginBottom: '0.75rem', marginTop: 0,
    }}>{children}</p>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" style={{
      color: '#818cf8', textDecoration: 'underline',
    }}>{children}</a>
  ),
  strong: ({ children }) => (
    <strong style={{ color: '#e2e8f0', fontWeight: 600 }}>{children}</strong>
  ),
  em: ({ children }) => (
    <em style={{ color: '#cbd5e1', fontStyle: 'italic' }}>{children}</em>
  ),
  hr: () => (
    <hr style={{ border: 'none', borderTop: '1px solid rgba(148,163,184,0.12)', margin: '1.5rem 0' }} />
  ),
  ul: ({ children }) => (
    <ul style={{
      margin: '0 0 0.75rem 0', paddingLeft: '1.25rem',
      fontSize: '0.825rem', color: '#94a3b8', lineHeight: 1.7,
    }}>{children}</ul>
  ),
  ol: ({ children }) => (
    <ol style={{
      margin: '0 0 0.75rem 0', paddingLeft: '1.25rem',
      fontSize: '0.825rem', color: '#94a3b8', lineHeight: 1.7,
    }}>{children}</ol>
  ),
  li: ({ children }) => (
    <li style={{ marginBottom: '0.2rem' }}>{children}</li>
  ),
  blockquote: ({ children }) => (
    <blockquote style={{
      margin: '0.75rem 0', padding: '0.6rem 1rem',
      borderLeft: '3px solid #6366f1',
      background: 'rgba(99,102,241,0.07)',
      borderRadius: '0 6px 6px 0',
      fontSize: '0.8rem', color: '#a5b4fc',
    }}>{children}</blockquote>
  ),
  code: ({ children, className }) => {
    const isBlock = className?.startsWith('language-')
    if (isBlock) {
      return (
        <code style={{
          display: 'block',
          background: 'rgba(0,0,0,0.35)',
          border: '1px solid rgba(148,163,184,0.1)',
          borderRadius: '6px',
          padding: '0.75rem 1rem',
          fontSize: '0.775rem',
          color: '#a78bfa',
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
          whiteSpace: 'pre',
          overflowX: 'auto',
          lineHeight: 1.6,
        }}>{children}</code>
      )
    }
    return (
      <code style={{
        background: 'rgba(167,139,250,0.1)',
        border: '1px solid rgba(167,139,250,0.15)',
        borderRadius: '4px',
        padding: '1px 5px',
        fontSize: '0.775rem',
        color: '#a78bfa',
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      }}>{children}</code>
    )
  },
  pre: ({ children }) => (
    <pre style={{ margin: '0.75rem 0', background: 'none', padding: 0 }}>{children}</pre>
  ),
  table: ({ children }) => (
    <div style={{ overflowX: 'auto', margin: '0.75rem 0' }}>
      <table style={{
        width: '100%', borderCollapse: 'collapse',
        fontSize: '0.8rem',
      }}>{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead style={{ borderBottom: '1px solid rgba(148,163,184,0.15)' }}>{children}</thead>
  ),
  th: ({ children }) => (
    <th style={{
      padding: '0.45rem 0.75rem', textAlign: 'left',
      fontSize: '0.72rem', fontWeight: 700,
      color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em',
    }}>{children}</th>
  ),
  td: ({ children }) => (
    <td style={{
      padding: '0.45rem 0.75rem',
      color: '#94a3b8',
      borderBottom: '1px solid rgba(148,163,184,0.07)',
    }}>{children}</td>
  ),
  tr: ({ children }) => (
    <tr style={{ transition: 'background 0.1s' }}
      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.02)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >{children}</tr>
  ),
}

export default function MarkdownDoc({ content }: MarkdownDocProps) {
  return (
    <div style={{ lineHeight: 1.6 }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
