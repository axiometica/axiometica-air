import ConsoleOutput from './ConsoleOutput'

interface RemediationStep {
  step: number
  tool: string
  description: string
  parameters: Record<string, any>
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped'
  command?: string     // actual CLI command returned by the backend
  raw_output?: string  // captured stdout/stderr from the real process
  message?: string     // interpreted/summary output
  duration?: string
  run_if?: string      // condition that was evaluated (shown on skipped steps)
}

interface RemediationStepsProps {
  steps: RemediationStep[]
  label?: string
}

// Resolve the primary target identifier from args.
// Handles process-kill tools (process_name), node-drain tools (node/target),
// deployment-restart tools (deployment/service), and template keys.
function resolveProcess(params: Record<string, any>): string {
  return (
    params.process_name ||
    params.process ||
    params.node ||
    params.node_name ||
    params.deployment ||
    params.service ||
    params.target ||
    ''
  )
}

// Build a human-readable command string from the tool name and its actual arguments.
function generateCommand(tool: string, params: Record<string, any>): string {
  const t = tool.toLowerCase().replace(/[()]/g, '').replace(/\s+/g, '_')
  const proc = resolveProcess(params)
  const sig  = (params.signal || 'SIGKILL').replace('SIG', '')

  switch (t) {
    // ── Real backend tools ──────────────────────────────────────────────────
    case 'process_kill':
      return proc
        ? `pkill -${sig} ${proc}  # force=${params.force ?? true}`
        : `pkill -${sig} <process>`
    case 'process_verify':
      return proc
        ? `pgrep -x ${proc}  # should_exist=${params.should_exist ?? false}`
        : `pgrep -x <process>`
    case 'container_monitor':
      return `docker stats --no-stream${proc ? ` | grep ${proc}` : ''}`
    case 'restart_service':
      return proc ? `docker restart ${proc}` : `systemctl restart <service>`
    case 'pod_restart':
      return `kubectl rollout restart deployment/${proc || '<deployment>'}`
    case 'kubectl_scale':
      return `kubectl scale deployment/${proc || '<deployment>'} --replicas=${params.replicas ?? 1}`
    case 'connection_drain':
      return `kubectl drain ${proc || '<node>'} --ignore-daemonsets --delete-emptydir-data`
    case 'dependency_check':
      return proc
        ? `pgrep -x ${proc} && lsof -p $(pgrep -x ${proc}) | grep -c LISTEN`
        : `netstat -tlnp  # dependency check`
    case 'syscall_profiler':
      return `bpftrace -e 'tracepoint:syscalls:sys_enter_* { @[comm] = count(); } interval:s:${params.timeframe_seconds || 5} { exit() }'`
    case 'process_info':
      return proc
        ? `ps -p $(pgrep -x ${proc}) -o pid,ppid,user,%cpu,%mem,cmd`
        : `ps aux --sort=-%cpu | head -10`

    // ── Legacy / generic tools ──────────────────────────────────────────────
    case 'trace_syscalls_ebpf':
      return `bpftrace -e 'tracepoint:syscalls:sys_enter_* { @[comm] = count(); } interval:s:${params.duration || 5} { exit() }'`
    case 'process_detail':
      return params.action === 'terminate'
        ? `pkill -${sig} ${params.process || '(process)'}`
        : `ps -p ${params.process || '1'} -o pid,ppid,user,%cpu,%mem,vsz,rss,cmd`
    case 'list_open_file_handles':
      return `lsof ${params.process ? `-p ${params.process}` : '-a'}`
    case 'memory_usage_breakdown':
      return `ps -p ${params.process || '1'} -o pid,%mem,rss,vsz,cmd`
    case 'cpu_usage_per_core':
      return `top -b -n ${params.samples || 1} -d ${Math.ceil((params.duration || 5) / (params.samples || 1))}`
    case 'container_status_overview':
      return `docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.CPUPerc}}\\t{{.MemPerc}}'`
    default: {
      // Clean args: drop template keys, keep concrete values
      const clean = Object.fromEntries(
        Object.entries(params).filter(([k]) => !k.includes('from_context'))
      )
      const snippet = JSON.stringify(clean)
      return snippet.length > 2
        ? `${t} ${snippet.length > 60 ? snippet.slice(0, 60) + '…' : snippet}`
        : t
    }
  }
}

export default function RemediationSteps({ steps, label }: RemediationStepsProps) {
  const sectionLabel = label ?? 'Remediation Steps'

  if (!steps || steps.length === 0) {
    return (
      <div style={{
        backgroundColor: '#1a1f2e',
        border: '1px solid #3d4557',
        borderRadius: '10px',
        padding: '20px 16px',
        textAlign: 'center',
      }}>
        <p style={{ fontSize: '12px', color: '#7a8ba3' }}>No {sectionLabel.toLowerCase()} recorded</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div style={{
        fontSize: '10px', fontWeight: 600, color: '#a0aec0',
        letterSpacing: '0.07em', textTransform: 'uppercase',
        paddingBottom: '8px',
        borderBottom: '1px solid #3d4557',
      }}>
        {sectionLabel} ({steps.length})
      </div>

      {steps.map((step) => (
        <ConsoleOutput
          key={step.step}
          step={step.step}
          tool={step.tool}
          command={
            (step.command && step.command.trim())
              ? step.command
              : generateCommand(step.tool, step.parameters)
          }
          rawOutput={step.raw_output}
          output={step.message || ''}
          duration={step.duration}
          status={step.status}
          runIf={step.run_if}
        />
      ))}
    </div>
  )
}
