/** Shared runbook utilities — used by both RunbookEditor and RunbookBrowser. */

export interface ToolParam {
  key: string
  label: string
  type: 'text' | 'number' | 'boolean' | 'select' | 'tags'
  placeholder?: string
  default?: any
  options?: string[]
  hint?: string
  autoResolved?: boolean
  required?: boolean
}

export interface ToolDef {
  tool: string
  label: string
  description: string
  commandTemplate?: string
  commandVariants?: Record<string, string>
  platforms?: string[]
  params: ToolParam[]
}

/** Replace {key} placeholders in a command template with actual arg values. */
export function interpolateCommand(template: string, args: Record<string, any> = {}): string {
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    const v = args[key]
    if (v === undefined || v === null || v === '') return `{${key}}`
    return Array.isArray(v) ? v.join(', ') : String(v)
  })
}

/** Render parameter key=value pairs for display (used in list/detail views). */
export function formatStepParams(args: Record<string, any> | undefined): string {
  if (!args || Object.keys(args).length === 0) return ''
  return Object.entries(args)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => {
      const label = k.replace(/_/g, ' ')
      const val = Array.isArray(v) ? v.join(', ') : String(v)
      return `${label}: ${val}`
    })
    .join(' · ')
}

/**
 * Build a map from step tool-name → incoming decision-condition annotation.
 * Requires source_steps (saved by the visual editor).
 * Decision nodes store routing in on_true/on_false (step IDs); target steps
 * are matched to flat diagnostics/actions by their tool field.
 */
export function buildConditionMap(
  sourceSteps?: { steps?: any[]; edges?: any[] } | null
): Map<string, { condition: string; branch: 'true' | 'false' }> {
  const result = new Map<string, { condition: string; branch: 'true' | 'false' }>()
  if (!sourceSteps?.steps) return result

  const byId = new Map(sourceSteps.steps.map((s: any) => [s.id, s]))

  // Pass 1: on_true / on_false fields on decision nodes (used by some runbooks)
  for (const step of sourceSteps.steps) {
    if (step.type !== 'decision') continue
    const condition = step.condition || ''
    const trueTarget = byId.get(step.on_true)
    if (trueTarget?.tool) result.set(trueTarget.tool, { condition, branch: 'true' })
    const falseTarget = byId.get(step.on_false)
    if (falseTarget?.tool) result.set(falseTarget.tool, { condition, branch: 'false' })
  }

  // Pass 2: edges with sourceHandle (used by runbooks where on_true/on_false are absent)
  const decisionIds = new Set(
    sourceSteps.steps.filter((s: any) => s.type === 'decision').map((s: any) => s.id)
  )
  for (const edge of (sourceSteps.edges ?? [])) {
    if (!decisionIds.has(edge.source)) continue
    if (edge.sourceHandle !== 'true' && edge.sourceHandle !== 'false') continue
    const src = byId.get(edge.source)
    const tgt = byId.get(edge.target)
    if (!src || !tgt?.tool) continue
    if (!result.has(tgt.tool)) {
      result.set(tgt.tool, { condition: src.condition || '', branch: edge.sourceHandle })
    }
  }

  return result
}
