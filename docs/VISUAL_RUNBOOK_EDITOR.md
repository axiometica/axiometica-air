# Visual Runbook Editor

**Version:** v1.2.0 · **Last updated:** June 2026  
**Audience:** ITOM admins, platform administrators, automation engineers

---

## Table of Contents

1. [Overview](#1-overview)
2. [Opening the Editor](#2-opening-the-editor)
3. [Canvas Interface](#3-canvas-interface)
4. [Node Types](#4-node-types)
5. [Connecting Nodes](#5-connecting-nodes)
6. [Properties Panel](#6-properties-panel)
7. [Output Capture & Variable Interpolation](#7-output-capture--variable-interpolation)
8. [Decision Nodes & Branching](#8-decision-nodes--branching)
9. [Conditional Steps (run_if)](#9-conditional-steps-run_if)
10. [Event Type Selection](#10-event-type-selection)
11. [Platform Selection & Command Variants](#11-platform-selection--command-variants)
12. [Live Test Execution](#12-live-test-execution)
13. [JSON Panel](#13-json-panel)
14. [Saving & Loading](#14-saving--loading)
15. [Importing Existing Runbooks](#15-importing-existing-runbooks)
16. [Keyboard Shortcuts & Tips](#16-keyboard-shortcuts--tips)

---

## 1. Overview

The Visual Runbook Editor is a canvas-based workflow builder for creating and editing remediation runbooks. It is built on [React Flow (xyflow)](https://reactflow.dev/) and opens in a dedicated browser tab at `/editor/`.

It complements the form-based editor in the main Runbook Library:

| | Form Editor | Visual Editor |
|---|---|---|
| Best for | Simple linear runbooks | Branching, conditional, or complex flows |
| Decision nodes | No | Yes |
| Output capture | No | Yes |
| Conditional steps | No | Yes |
| Live test execution | No | Yes |
| JSON preview | No | Yes (live) |
| Platform command variants | Yes | Yes |

Both editors read and write to the same backend API — a runbook can be started in one and continued in the other.

---

## 2. Opening the Editor

**New runbook (blank canvas):**  
Runbooks page → **New Runbook** button → opens `/editor/` in a new tab.

**Edit existing runbook:**  
Runbooks page → hover a card → **Visual Editor** icon  
_or_  
Form editor → **Open in Visual Editor** button (appears only when editing an existing runbook)

The editor loads at `/editor/?id={runbook_id}` when editing. On first load it reconstructs the full canvas from the stored runbook including node positions and edge routing.

---

## 3. Canvas Interface

```
┌─────────────────────────────────────────────────────────────┐
│  Toolbar ── Name | Trigger (event type) | Platform | Meta   │
├──────────┬──────────────────────────────────┬───────────────┤
│          │                                  │               │
│ Sidebar  │         Canvas                   │  Properties   │
│ (nodes)  │   (drag, connect, arrange)       │  Panel        │
│          │                                  │               │
│          │                                  │               │
├──────────┴──────────────────────────────────┴───────────────┤
│  Footer ── Save | Test | JSON toggle | Validate             │
└─────────────────────────────────────────────────────────────┘
```

**Canvas controls:**
- **Pan** — drag on empty canvas background
- **Zoom** — scroll wheel or pinch; or use the zoom controls (bottom-left)
- **Select node** — click; shift-click to multi-select
- **Move node** — drag
- **Delete node/edge** — select then `Backspace` or `Delete`
- **Minimap** — bottom-right corner; click to jump to a region

The canvas always starts with two locked nodes: **START** and **END**. These cannot be deleted. Every valid runbook must have a path from START to END.

---

## 4. Node Types

Drag any node type from the left sidebar onto the canvas.

### START / END
Fixed entry and exit points. Not configurable. Every execution path must begin at START and reach END.

### Diagnostic
**Purpose:** Read-only information gathering — logs, metrics, health probes, container inspection.  
**on_failure default:** `continue` — a failed diagnostic does not abort the runbook.  
**Fields:** Description, Tool (from Approved Actions catalogue), Parameters, Command (editable), Output Capture.

### Action
**Purpose:** Mutating corrective step — restart, process kill, flush cache, scale pods.  
**on_failure default:** `abort` — the runbook halts if the action fails.  
**Fields:** Description, Tool, Parameters, Command (editable), `run_if` condition.

### Verification
**Purpose:** Post-action health check confirming the incident condition cleared.  
**on_failure default:** `abort`.  
**Fields:** Description, Metric, Operator (`< <= > >= = ≠ contains starts_with ends_with`), Threshold value.

Example: `metric=cpu_percent`, `operator=less_than`, `value=75` → passes if CPU dropped below 75% after the action.

### Decision
**Purpose:** Branch execution based on a condition evaluated at runtime.  
**Outputs:** Two edges — **true** (green) and **false** (red).  
**Field:** `condition` — a Python-style expression evaluated against the execution context.

```
Examples:
  disk_percent > 90
  service_status == "degraded"
  retry_count < 3
```

See §8 for branching details.

### Wait
**Purpose:** Pause execution for a fixed number of seconds between steps.  
**on_failure default:** N/A — always succeeds.  
**Fields:** Duration (seconds).  
Useful when a prior action (e.g. process restart) needs time to take effect before the next diagnostic or verification step runs.

### Notify
**Purpose:** Emit a mid-workflow notification — escalate, acknowledge, resolve, or message — via PagerDuty, Slack, email, or webhook.  
**on_failure default:** `continue` — notification failures do not abort the runbook.  
**Fields:** Description, Tool (`notify`), Parameters — `action` (`escalate` / `acknowledge` / `resolve` / `message`), `team` (optional, autocompletes against the Notification Teams registry in Settings — routes to that team's configured channels instead of the platform defaults), `message`, `severity`.  
**Legacy aliases:** `alert_escalate`, `alert_update`, `send_alert` still work and map onto the same handler, but `notify` is the recommended tool for new runbooks.  
Notify steps render in their own "Notifications" section in the incident UI (not under Remediation) and always run even when the runbook is executed in diagnostics-only mode.

---

## 5. Connecting Nodes

Draw an edge by hovering over a node until the **connection handle** (small circle) appears on its edge, then drag to the target node's handle.

- Most nodes have one output handle (bottom) and one input handle (top).
- **Decision nodes** have two output handles: **true** (right/green) and **false** (left/red). Connect each to the appropriate downstream node.
- Edges can be **deleted** by clicking them and pressing `Backspace`.

A runbook is **valid** when every non-END node has at least one outgoing edge and every non-START node has at least one incoming edge. The **Validate** button in the footer highlights nodes with missing connections in red.

---

## 6. Properties Panel

Click any node to open its properties in the right panel.

### Common fields (all non-trivial node types)

| Field | Description |
|---|---|
| **Name** | Short identifier shown on the canvas node chip |
| **Description** | Longer explanation — appears in execution logs |
| **Tool** | Approved action to execute (searched from the catalogue) |
| **Command** | Actual shell invocation — auto-filled from the tool template, fully editable |
| **on_failure** | `abort` (halt runbook) or `continue` (skip and proceed) |

### Tool selection

The tool dropdown shows only actions from the **Approved Actions catalogue** that are compatible with the runbook's selected platform. Selecting a tool:
1. Populates the **command** field with the platform-specific template
2. Shows typed **parameter fields** (text, number, boolean, select, tags) below the command
3. Parameter values are interpolated into `{param_name}` placeholders in the command automatically

### Parameter fields

Each parameter defined in the Approved Action record renders as the appropriate input type. Required parameters are marked `*`. The resolved command updates live as you edit parameters.

---

## 7. Output Capture & Variable Interpolation

Output capture lets you extract values from a step's structured output and pass them as named variables to later steps.

### Defining output capture

In the **Properties Panel** for a Diagnostic or Action node, scroll to the **Output Capture** section:

| Column | Value |
|---|---|
| Variable name | The name you will reference later, e.g. `disk_percent` |
| JSONPath | Expression to extract from the step's JSON output, e.g. `$.usage_percent` |

Click **Add Capture** to add a row. Multiple captures per step are supported.

Many built-in tools also emit **automatic outputs** without any capture configuration — e.g. `check_container_status` always emits `container_status`, `container_running`, `container_restart_count`, etc. These are listed in the **Available Variables** section of the Properties Panel for downstream nodes.

### Two contexts, two syntaxes

Variables are used in two different contexts and each uses a different syntax:

#### In conditions (Decision node `condition` field and `run_if`)

The condition evaluator supports three field reference formats:

| Format | Example | When to use |
|---|---|---|
| `step_id.field` | `diag_container_status.container_status == running` | **Preferred** — explicit reference to a specific node by its ID |
| `step_N.field` | `step_2.container_status == running` | Legacy — references step by execution order index; fragile if steps are reordered |
| `field` (bare) | `container_status == running` | Shorthand — scans all prior step outputs for the first match; use when the field name is unique |

The **Available Variables** chips in the Properties Panel insert the preferred `step_id.field` form. Click or drag a chip to insert it at the cursor position in the condition field.

Special built-in fields always available in conditions (no capture needed):

| Field | Value |
|---|---|
| `top_process` | Name of the highest-CPU process found by the most recent `top_processes` step |
| `anomaly_process` | Process name from the incident alert payload |
| `container` | Target container/resource name |
| `context.severity` | Incident severity string |
| `context.risk_score` | Incident risk score (float) |

#### In step parameter values (Args fields)

To feed a prior step's output value into a parameter, use **double curly braces**:

| Format | Example | When to use |
|---|---|---|
| `{{step_id.field}}` | `{{verify_service.http_code}}` | **Preferred** *(v1.5.0)* — explicit reference to a specific node by its ID, same convention as the condition syntax above. Avoids ambiguity when multiple steps emit a field with the same name |
| `{{field}}` | `{{container_status}}` | Flat lookup — searches all prior step outputs for the key |
| `{{steps.N.field}}` | `{{steps.2.container_status}}` | Indexed lookup by execution order |

The **Available Variables** chips in the Properties Panel can be dragged directly onto any Args value field to insert the `{{step_id.field}}` form at the cursor — the same drag-and-drop already available for conditions.

> **Note:** Single braces `{param_name}` are the tool command template syntax — the platform substitutes these with the parameter values you set in the Args section. Double braces `{{variable}}` are the runbook variable syntax — the workflow engine resolves these from prior step outputs before the tool command is built.

#### Incident context variables

When running from the Test Run bar, the **Incident Context** fields (`service_url`, `process_name`, `anomaly_process`) are also available as flat `{{variable}}` references in step args and as bare field names in conditions:

| Context field | In conditions | In args |
|---|---|---|
| `service_url` | `service_url != ""` | `{{service_url}}` |
| `process_name` | `process_name == nginx` | `{{process_name}}` |
| `anomaly_process` | `anomaly_process == python3` | `{{anomaly_process}}` |

Step outputs always take priority over incident context when both define the same key.

### Example: full chaining flow

```
# Node: diag_container_status (Diagnostic, tool: check_container_status)
# → automatically emits: container_status, container_running

# Node: dec_container_status (Decision)
# Condition (preferred):   diag_container_status.container_status == running
# Condition (also valid):  container_status == running

# Node: action_restart (Action, tool: start_web_server)
# Args:  process_name → {{process_name}}   ← resolved from incident context
#        url          → {{service_url}}     ← resolved from incident context

# Node: verify_health (Verification)
# Metric: http_code, check: >=, value: 200
```

Variables accumulate across the execution and are available to all downstream nodes. If two steps capture the same field name, the later step's value wins.

---

## 8. Decision Nodes & Branching

Decision nodes evaluate a boolean expression and route execution down the **true** or **false** path.

### Wiring a decision

1. Drag a **Decision** node onto the canvas
2. Set the **condition** in the Properties Panel
3. Connect the **true handle** (green, right side) to the "yes" branch
4. Connect the **false handle** (red, left side) to the "no" branch
5. Both branches must eventually reach **END** (or reconnect to a shared downstream node)

### Condition expressions

Conditions are evaluated against the accumulated step output context. The recommended field reference format is `step_id.field` (see §7 for the full syntax reference):

```
# Preferred: step_id.field — unambiguous
diag_container_status.container_status == running
diag_http_check.http_code != 200
diag_check_disk.disk_percent > 90

# Also valid: bare field name (scans all prior outputs)
container_status == running
disk_percent > 90

# Numeric
cpu_percent <= 85
retry_count < 3

# Membership
container_status IN [running, restarting]
environment NOT IN [production, staging]

# Built-in context
top_process == nginx
anomaly_process != ""
context.severity == critical
```

Supported operators: `==  !=  >  <  >=  <=  IN  NOT IN`

### Convergence

Both branches can merge back into a single downstream path by connecting both branch endpoints to the same node. The BFS layout algorithm handles this automatically when loading saved runbooks.

---

## 9. Conditional Steps (run_if)

Unlike Decision nodes (which fork the graph), `run_if` is a per-step gate that skips the node if the condition is false — without forking execution.

Set the **run_if** field in the Properties Panel of any Action or Diagnostic node. It uses the same condition syntax as Decision nodes (see §8):

```
# Skip restart if web server process is already running
run_if: diag_check_process.process_exists == false

# Only clean logs if disk is actually full
run_if: diag_check_disk.disk_percent > 90

# Bare field name also works (searches all prior outputs)
run_if: container_running == false
```

When a step is skipped due to `run_if`, execution continues to the next node as if the step had passed.

---

## 10. Event Type Selection

The **Trigger (event type)** field in the toolbar header determines which incidents this runbook is eligible for. It uses a **searchable combobox** backed by the live event type taxonomy (`/api/event-types`).

- Start typing to filter by code, label, or domain
- Select from the dropdown or type a full code and press Enter
- The selected code is shown in monospace

The taxonomy contains 210 pre-seeded canonical types. See [ADMIN_GUIDE.md §13](ADMIN_GUIDE.md) for managing custom types.

---

## 11. Platform Selection & Command Variants

The **Platform** dropdown in the toolbar header controls which command variant the engine uses when executing the runbook:

| Value | Target environment |
|---|---|
| `any` | Matches all resources (default) |
| `docker` | Containerised workloads — `docker exec` / `docker restart` |
| `linux` | Bare-metal or VM hosts via SSH + `systemctl` |
| `windows` | Windows hosts via WinRM `Invoke-Command` |
| `kubernetes` | K8s clusters — `kubectl` commands |

When a tool has platform-specific command variants defined in its Approved Action record, the editor automatically resolves the correct variant for the selected platform. The resolved command is shown in the command field and can be edited.

At runtime, the MechanicAgent re-resolves the variant against the target resource's detected platform — a runbook set to `any` will use the `docker` variant for Docker resources and the `linux` variant for SSH resources.

---

## 12. Live Test Execution

The **Test Run** button in the toolbar opens a test bar that runs the runbook directly against a target container without creating an incident or going through the approval pipeline.

### Opening the Test Run bar

Click **Test Run** in the toolbar. A bar appears below the toolbar with these controls:

| Control | Description |
|---|---|
| **Watcher** | Which watcher agent routes the commands (default: `watcher_brain`) |
| **Target** | Container or resource name to execute against (e.g. `my_service`) |
| **On fail** | `Continue all` — always advance; `Stop actions` — halt on action failure; `Stop all` — halt on any failure |
| **⚙ Context** | Toggle to show incident context fields (see below) |
| **Dry Run** | Toggle — when on, action steps are skipped and show resolved args; diagnostics still run |
| **Run on Target** | Execute the runbook. Button label changes to **Dry Run** when Dry Run is active |

### Incident context fields

Click **⚙ Context** to expand a second row with three fields:

| Field | Placeholder description |
|---|---|
| `service_url` | HTTP URL of the service (fills `{{service_url}}` in step args and `{service_url}` in commands) |
| `process_name` | Process to target (fills `{{process_name}}`) |
| `anomaly_process` | Anomaly process from the alert (fills `{{anomaly_process}}`) |

The context button shows a dot (`⚙ Context ●`) when any field is non-empty as a reminder that values are set.

These values substitute into step arg templates (e.g. `{{process_name}}` resolves to `nginx`) and into tool command placeholders (e.g. `{process_name}` in the shell command). See §7 for the full variable syntax.

### Dry Run mode

Enable **Dry Run** before clicking the run button to validate the runbook without executing any mutating actions:

- **Diagnostic** steps execute normally — you see real output and captured values
- **Verification** steps execute normally — you see real metric readings
- **Action** steps are skipped — the result panel shows a **DRY RUN** badge and a "Resolved args" box with all parameter values after template substitution (so you can confirm `{{process_name}}` resolved to `nginx`, etc.)
- **Wait** steps skip the sleep delay

Use Dry Run to verify step chaining and argument resolution before running against production.

### How execution works

1. Click **Run on Target** (or **Dry Run**)
2. Each canvas node transitions through states as it executes:
   - `pending` (grey) → `running` (blue pulse) → `success` (green) / `failed` (red) / `skipped` (grey dash)
3. Decision edges light up green (true branch taken) or red (false branch)
4. The **Live Execution** panel on the right shows results as they arrive, one step at a time
5. Clicking any canvas node during or after a run opens its Properties panel; closing it returns to the results panel

### Live Execution panel

| Badge / icon | Meaning |
|---|---|
| `✓` green | Step succeeded |
| `✗` red | Step failed |
| `—` grey | Step skipped (`run_if` was false) |
| `⊘` amber + **DRY RUN** | Action step skipped in dry-run mode |
| `⟳` spinning | Step currently running |

Each step row shows: the resolved command, truncated raw output (first 5 lines), and structured captured values. The summary footer shows succeeded / skipped / failed counts.

### Notes

- The runbook does **not** need to be saved before running — the current canvas state is sent directly
- Execution goes directly to the watcher, bypassing Celery and the incident approval pipeline
- Action steps execute for real unless Dry Run is enabled — test against a safe/non-production resource

---

## 13. JSON Panel

Click the **{ }** toggle in the footer to open the JSON panel alongside the canvas.

The panel shows the live runbook JSON as it will be sent to the API. It updates in real time as you add nodes, edit properties, and draw edges. Use it to:

- **Verify the structure** before saving
- **Copy the JSON** for version control or manual import
- **Debug condition expressions** — confirm your `run_if` and decision `condition` strings look correct

The JSON format mirrors the API's runbook schema. `steps` are listed in execution order (topological sort from START to END). Decision steps include `on_true` and `on_false` fields referencing the target step IDs.

---

## 14. Saving & Loading

### Save

Click **Save Runbook** in the footer. The editor:
1. Validates the graph (all nodes connected, name and event type filled)
2. Serialises nodes + edges to the runbook JSON schema
3. Calls `POST /api/runbooks` (new) or `PATCH /api/runbooks/{id}` (existing)
4. Shows a success toast; the URL updates to `?id={runbook_id}` if newly created

Node positions are saved alongside the runbook definition, so the canvas layout is restored exactly when reopening.

### Load

Open `/editor/?id={runbook_id}` to load an existing runbook. The editor:
1. Fetches the runbook from `GET /api/runbooks/{id}`
2. Reconstructs nodes from `source_steps` (the editor's own saved step format) if available, or falls back to converting the runbook's `diagnostics / actions / verification_steps` arrays
3. Runs BFS auto-layout to position nodes if position data is missing

---

## 15. Importing Existing Runbooks

All runbooks in the library — including the 100+ seeded entries — are fully editable in the visual editor. From the **Runbook Library**:

1. Hover a card → click the **Visual Editor** icon (grid/flow icon)
2. The editor opens at `/editor/?id={runbook_id}`
3. The BFS layout algorithm positions nodes automatically based on the stored step order and decision branches
4. Edit as needed and **Save Runbook** to update

The conversion from the flat `diagnostics / actions / verification_steps` arrays to canvas nodes preserves all tool, args, command, and metric fields. Decision nodes (if the runbook was originally created in the visual editor) are reconstructed with their true/false edge routing intact.

---

## 16. Keyboard Shortcuts & Tips

| Action | Shortcut |
|---|---|
| Delete selected node / edge | `Backspace` or `Delete` |
| Select all | `Ctrl+A` |
| Zoom to fit | Double-click empty canvas background |
| Pan | Hold `Space` + drag, or middle-mouse drag |
| Cancel connection drag | `Escape` |

**Tips:**

- **Name nodes clearly** — names appear as chips on the canvas and in execution logs. Use action-oriented names: `Check Disk Usage`, `Restart Service`, `Verify CPU Normal`.
- **Use Decision nodes sparingly** — one or two per runbook is common; a deeply nested decision tree is usually better split into separate runbooks triggered by different event types.
- **Output capture variable names** must be alphanumeric + underscore and unique within the runbook. They accumulate across the execution and the last-written value wins if two steps capture the same name.
- **Validate before saving** — the Validate button highlights nodes missing connections in red. A runbook that fails validation can still be saved but will fail at runtime if an unreachable node is reached.
- **The JSON panel is authoritative** — if the canvas looks wrong, check the JSON panel to see what will actually be saved.
