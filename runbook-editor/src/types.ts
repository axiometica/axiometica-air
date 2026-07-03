export type StepType = 'start' | 'end' | 'diagnostic' | 'action' | 'verification' | 'decision' | 'notify' | 'wait' | 'incident_update';

export interface OutputCapture {
  [varName: string]: string; // varName → JSONPath e.g. "disk_percent" → "$.usage_percent"
}

export interface RunbookStepData extends Record<string, unknown> {
  id: string;
  stepType: StepType;
  name: string;
  tool?: string;
  description?: string;
  args?: Record<string, string>;
  outputCapture?: OutputCapture;
  run_if?: string;
  // Step failure policy — respected by the real incident workflow executor.
  // "abort" (default) — halt the runbook if this step fails.
  // "continue"        — skip the failure and advance to the next step.
  on_failure?: 'abort' | 'continue';
  // verification
  metric?: string;
  check?: string;
  value?: string;
  // wait
  duration_seconds?: number;
  // incident_update — sets the incident's lifecycle state. Only reachable if every
  // step before it (including verification) succeeded — on_failure=abort (the
  // default) halts the run before reaching it otherwise, so this needs no run_if.
  state?: string;
  // retry (action steps only)
  retry_count?: number;
  retry_delay_seconds?: number;
  // decision
  condition?: string;
  on_true?: string;
  on_false?: string;
  // runtime (live execution state)
  status?: 'pending' | 'running' | 'success' | 'skipped' | 'failed';
  liveOutput?: Record<string, string>;
  // validation overlay — set by App.tsx validateGraph(), never persisted
  hasError?: boolean;
}

export interface RunbookJSON {
  name: string;
  trigger_type: string;
  description: string;
  platform: string;
  service?: string;
  confidence: number;
  blast_radius: number;
  enabled: boolean;
  steps: RunbookStep[];
}

export interface RunbookStep {
  id: string;
  type: StepType;
  name?: string;
  tool?: string;
  description?: string;
  args?: Record<string, string>;
  output_capture?: OutputCapture;
  run_if?: string;
  on_failure?: 'abort' | 'continue';
  metric?: string;
  check?: string;
  value?: string;
  duration_seconds?: number;
  retry_count?: number;
  retry_delay_seconds?: number;
  condition?: string;
  on_true?: string;
  on_false?: string;
  state?: string;
}
