import { useState } from 'react';

interface Props {
  json: Record<string, unknown>;
}

export function JsonPanel({ json }: Props) {
  const [copied, setCopied] = useState(false);
  const jsonStr = JSON.stringify(json, null, 2);

  const copy = () => {
    navigator.clipboard.writeText(jsonStr);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', background: '#0d0f14' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderBottom: '1px solid #1e2a3a', flexShrink: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0', flex: 1 }}>Runbook Definition</div>
        <button onClick={copy} style={btn('#2d3450', '#94a3b8')}>{copied ? '✓ Copied' : 'Copy'}</button>
      </div>

      {/* JSON */}
      <pre style={{
        flex: 1, overflow: 'auto', margin: 0, padding: '14px 16px',
        fontSize: 11.5, fontFamily: "'Cascadia Code', Consolas, monospace",
        color: '#94a3b8', lineHeight: 1.6, background: 'transparent',
        whiteSpace: 'pre-wrap', wordBreak: 'break-all',
      }}>
        {jsonStr.split('\n').map((line, i) => (
          <span key={i} dangerouslySetInnerHTML={{ __html: colorJson(line) + '\n' }} />
        ))}
      </pre>
    </div>
  );
}

function colorJson(line: string): string {
  return line
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/("(?:id|type|name|tool|condition|metric|check|run_if|on_true|on_false|output_capture|source|target|graph_edges|graph_positions)")/g, '<span style="color:#a78bfa">$1</span>')
    .replace(/"(diagnostic|action|verification|decision|notify|start|end)"/g, '<span style="color:#f59e0b">"$1"</span>')
    .replace(/: (true|false|null)/g, ': <span style="color:#f43f5e">$1</span>')
    .replace(/: (\d+(?:\.\d+)?)/g, ': <span style="color:#10b981">$1</span>')
    .replace(/: "([^"]*)"/g, (_, v) => `: <span style="color:#3b82f6">"${v}"</span>`);
}

function btn(bg: string, color: string): React.CSSProperties {
  return {
    background: bg, border: 'none', borderRadius: 6, color,
    fontSize: 11, fontWeight: 600, padding: '5px 11px', cursor: 'pointer',
  };
}
