# Changelog

All notable changes to Axiometica AIR are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

- Kubernetes Helm charts for cloud-native deployments
- Distributed tracing via OpenTelemetry + Jaeger
- Advanced analytics dashboard (MTTR trend lines over time, service health scorecards)
- Multi-tenancy support

---

## [1.5.0] — 2026-06-29

### New Features ✨

**Outbound PagerDuty Escalation**
- `alert_escalate` / `alert_update` now make real PagerDuty Events API v2 calls (trigger / acknowledge / resolve) instead of running a no-op shell command. The PagerDuty connector config gained an encrypted `routing_key` field, set from Connector Hub
- New `PagerDutyEventsClient` (sync + async) under `connectors/pagerduty/`

**Notification Teams — Explicit Team-Based Routing**
- New standalone `notification_teams` registry: each team carries any combination of a PagerDuty routing key, Slack channel, email recipient list, and webhook URL/secret — independent of ServiceNow/CMDB, so it works whether or not that integration is configured
- A runbook's `notify` step routes to a named team's channels when `team` is given (case-insensitive match); falls back to the platform's global Slack/PagerDuty/email defaults when the team is omitted, unknown, or disabled
- CRUD UI: Settings → Notification Teams. CRUD API: `/api/notification-teams`
- Both runbook editors (the inline form editor and the graph-based visual editor) gained a live autocomplete on the `team` argument, sourced from the same registry

**Unified `notify` Action**
- New primary catalog tool `notify` with an explicit `action` param (`escalate` / `acknowledge` / `resolve` / `message`) replaces ad-hoc per-purpose tools, with channel-type rules per action — `escalate` fans out to every configured channel, `acknowledge`/`resolve` touch PagerDuty only, `message` skips PagerDuty so an informational note never opens a page
- `alert_escalate`, `alert_update`, and `send_alert` remain as thin, backward-compatible aliases onto the same handler
- Fixed `send_alert`'s catalog entry, which claimed to deliver to Slack/webhook/log but actually ran `echo "alert: {message}"` on the target host and delivered nothing

**`{{step_id.field}}` Template References in Runbook Args**
- Runbook step `args`/`message` fields can now reference a specific upstream step's output by its editor-assigned ID — e.g. `{{verify_service.http_code}}` — matching the syntax already used by `run_if`/condition fields, removing the previous ambiguity of the bare `{{field}}` form when multiple steps emit the same field name
- Both runbook editors gained drag-and-drop variable chips wired into this syntax for step args (previously only available for conditions)

### Bug Fixes 🐛

**Notify Steps Miscategorized as Remediation**
- `notify`/`alert_escalate`/`alert_update`/`send_alert` steps were flattened into the same `actions` array as real remediation steps at save time, with no distinguishing tag at execution time — they showed up under "Remediation Steps" in the incident UI and were incorrectly skipped in diagnostics-only mode (which intentionally stops before remediation)
- Notify steps now resolve to their own `step_type` regardless of which array they're stored in, render in a dedicated "Notifications" section, and are exempted from the diagnostics-only skip

**Slack Failures Reported as Generic "Not Configured"**
- A Slack post that failed because the channel rejected it (bot not invited, channel archived/renamed, etc.) was reported identically to Slack being completely unconfigured, since `_post_slack()` returned a bare `bool`. It now returns the specific reason (e.g. `not_in_channel`) via `slack_sdk`'s `SlackApiError.response["error"]`

**Incident Context Missing from Notify Messages**
- `notify` messages now auto-prefix with incident number, title, and runbook name when available — `INC0046 - <title> - <runbook> - <message>` — so a Slack alert from a runbook step reads the same way as the platform's other incident notifications

**Settings Forms — Placeholder Text Indistinguishable from Real Values**
- Inputs styled via inline `style` (rather than the `.form-input` class) fell back to the browser's default placeholder color, which on this dark theme was close enough in brightness to real filled-in text that an empty field showing example placeholder text (e.g. `#incidents`) could be mistaken for an already-saved value. Added a global placeholder-color fallback so every input/textarea gets a clearly muted placeholder regardless of how it's styled

**Secret Decryption Misses (ServiceNow Auto-Push, Webhook Auth)**
- `auto_push_if_configured`, `alert_webhooks._validate_secret`, and the Splunk webhook token check all read encrypted connector secrets directly from `config_json` without decrypting them, causing real auto-push 401s and webhook auth failures once those secrets started being encrypted at rest

**Qualification Weights Never Refreshed After Settings Save**
- `EventQualificationService` loaded its scoring weights from the database exactly once per backend process lifetime. Saving new weights via Settings → Incident Qualification wrote to the database but had no effect on the already-running backend — every event kept scoring against stale weights until a full restart. Settings saves now reload the live singleton immediately

### Internal 🧹
- Removed ~1,400 lines of unreferenced dead code (`ToolRegistryAgent._simulate_tool_output`, confirmed zero callers repo-wide) — the real tool-dispatch path is `_execute_tool` → `_execute_tool_impl`
- Event-type override field in Settings → Incident Qualification gained search/autocomplete against the ~200-entry taxonomy (previously bare free text, where a typo silently created a dead override that never matched anything)

---

## [1.4.0] — 2026-06-25

### New Features ✨

**Draft / Publish Workflow + Version History — Runbooks & Policies**
- Edits now land in `draft_snapshot` only; nothing reaches the live, execution-facing columns until an explicit **Publish**. `enabled` stays a separate, instant kill-switch, independent of draft state
- Each publish writes a version row (`runbook_versions` / `policy_versions`); the editor UI gained a version-history panel with restore-to-draft
- New migration `0011` → `0012` adds `status` / `published_at` / `has_unpublished_changes` / `draft_snapshot` to both tables

**New Step Type — `incident_update`**
- Declares the incident's resolution state (e.g. `resolved`) and is the *only* signal `VerifierAgent` trusts to mark an incident resolved. It's only reachable if every step before it — including verification — succeeded, since `on_failure: abort` (default on every step) halts execution otherwise
- Graph editor support: palette entry, properties panel state dropdown, node rendering
- Publish-time non-blocking warning when an action-containing path never reaches verification → `incident_update`
- AI runbook generation updated to produce this pattern by default

### Bug Fixes 🐛

**False-Positive Incident Resolution**
- The executor's verification step never re-ran its own tool — it only ever looked up pre-existing values from earlier diagnostics, so a verification node's intended post-remediation "_after" measurement was never actually taken. Incidents could resolve off a stale or assumed value instead of confirming the problem was actually fixed
- Verification nodes that carry a `tool` now re-execute it for a fresh measurement (matching what the editor's Test Run harness already did); a metric that still can't be measured fails closed instead of silently defaulting to "passed" — fixed identically in both the real executor and the Test Run harness, which had drifted out of sync on this exact behavior
- `VerifierAgent` no longer infers resolution from `execution_result.success` (previously with a `process_kill`-specific recheck) — replaced by the explicit `incident_update` signal above
- All 15 seeded runbooks' graphs updated accordingly, and `runbooks_seed_data.py` (the fresh-install seed source) synced to match — 9 of 15 had no graph (`source_steps`) at all in the seed file, meaning a fresh install would not have gotten this fix
- `platform_reset` now also clears runbook execution-feedback stats, which were accumulated under the broken logic above

**State Propagation Across Agents**
- `WorkflowState.set_context()` only preserves a fixed allowlist of untyped context keys when syncing from the typed context; `incident_update_requested` (the resolution signal above) and `runbook_graph` were missing from it, so a later agent's context sync could silently erase a signal an earlier agent had just set in the same workflow run
- Watcher external-check alerts (`ping_failed`, `external_http_failed`, `external_tcp_failed`, `dns_failed`, `tls_expiry`) carry the real checked URL under `check_url`/`port`, but runbook step substitution only recognized `service_url`/`service_port`, and only matched the single-brace `{service_url}` placeholder form rather than the `{{service_url}}` convention used everywhere else. Net effect: `{{service_url}}` args were never substituted for incidents from these event types, so tools ran against a literal unresolved placeholder and verification failed regardless of actual service health

**Install / Seed Scripts**
- Removed the legacy raw-SQL runbook seeding (`backend/seeds/*.sql`) from `setup_oob.py` — fully superseded by `runbooks_seed_data.py`, which runs automatically on every backend startup and is the source kept in sync with the fixes above. The SQL path's one `ON CONFLICT DO UPDATE` clause would otherwise overwrite corrected runbook data with stale content (mismatched tool references) on every install run

---

## [1.3.0] — 2026-06-23

### New Features ✨

**Runbook Confidence Gate — Pin to a Specific Runbook**
- Policies with a Confidence Gate enabled can now optionally pin the gate to one specific, named runbook (`confidence_gate_runbook_id`) instead of trusting whichever runbook the event_type/service/platform lookup cascade resolves at execution time
- New migration `0011` adds `confidence_gate_runbook_id` (FK → `runbooks.id`, `SET NULL` on delete) to `policies`
- Policy Editor's Confidence Gate section gained a "Specific Runbook" dropdown sourced live from `/runbooks`, showing each runbook's confidence % and success rate so the operator can judge trust before pinning
- `incident_agents.py`'s gate-evaluation logic checks the pinned runbook first via direct ID lookup, falling back to the existing 4-pass cascade when unset

**Governance Policy Matching — Event Type + Min Risk Score**
- Policies can now match on **Event Type** as a searchable multi-select sourced from the 212-entry Event Type Taxonomy (supports domain wildcards, e.g. `infrastructure.*`)
- Fixed: `min_risk_score` was captured and persisted by the Policy Editor but never actually evaluated in the policy-matching loop — incidents below the configured risk floor were incorrectly matched. Now enforced.

**Approved Actions — Live Catalogue**
- Policy Editor's "Approved Actions" picker now sources live from the real `ApprovedActionModel` catalogue (42 actions across `remediation_safe` / `remediation_intrusive` / `notify`), grouped by category with blast-radius badges — replaces a hardcoded 8-item list, 3 of which referenced actions that did not exist in the catalogue and could never match a real `proposed_action`

**Policy Priority Repositioning**
- "Approval Priority" renamed to **Policy Priority** and moved to the top of the Create/Edit Policy form, with inline guidance that precedence is decided solely by this number (lowest wins) — more specific match criteria do **not** automatically take precedence over a generic policy

### Bug Fixes 🐛

**MTTR Breakdown — Incorrect Weighted Average and Misleading Model**
- Reworked the dashboard's MTTR Breakdown card from a 2-column "Auto vs Manual" model (with a derived "approval adds X delay" metric that didn't hold up under scrutiny) into 3 honest buckets: Auto · No Approval, Auto · With Approval, Manual
- Fixed: the summary strip picked one auto-path's average MTTR via a `??` fallback instead of computing a true count-weighted average across both auto paths
- Fixed: `watcher_all_clear` incidents carrying a stale leftover approval row from earlier in their lifecycle were silently dropped from all three buckets instead of counting as `no_approval`; verified against production data (174 no_approval + 14 with_approval + 4 manual = 192, matching the medium-severity total exactly — previously only 33/192 were captured)

---

## [1.2.0] — 2026-06-12

### New Features ✨

**Visual Runbook Editor**
- New graph-based runbook editor with drag-and-drop step authoring
- Decision nodes with conditional routing via edge connections
- Live execution with step-by-step animated results
- Undo/redo, edge highlighting, graph validation
- Variable pills with upstream-only insertion (drag-to-insert)
- Load/save and canvas position persistence

**Hierarchical Event-Type Taxonomy**
- 210 event types across 9 domains (compute, storage, network, database, etc.)
- Taxonomy seeded automatically on platform setup
- Runbook tool picker now loads approved actions dynamically from DB

**Performance**
- Frontend build time cut from 55s → 17s

### Bug Fixes 🐛

- Celery worker CPU burn resolved (replaced `celery inspect ping` healthcheck with `pgrep`)
- AI Insights tab crash on storm incidents (React Error #31 — topology evidence objects rendered as JSX)
- Root cause candidates showing blank names on storm detail
- Celery zombie processes — added `init: true` to worker containers
- LLM retry backoff to handle transient API failures
- ServiceNow CMDB CI lookup and Cypher syntax fixes
- Verification step label showing "unknown" in executor
- Verification step threshold field mismatch
- SQLAlchemy `::jsonb` cast error in setup

---

## [1.1.2] — 2026-06-07

### Bug Fixes 🐛

**Disk Usage Always Showing 0% (Watcher Monitoring Dashboard)**
- Root cause: `_record_metrics_snapshot()` was resolving container disk usage via container name lookup against the Docker stats JSON key, which uses a truncated/mangled name rather than the actual container name; the lookup always missed, producing 0%
- Fix: `_record_metrics_snapshot()` now receives a pre-built `disk_map: Dict[str, float]` argument directly from the caller rather than re-deriving it; eliminates the name mismatch
- Commit: `2284809`

**"Active (All Open)" Incident Filter Returning HTTP 500**
- Root cause: `'investigating'` was included in `_ACTIVE_STATES` in `workflows.py`; this is not a valid `LifecycleState` enum value, causing SQLAlchemy to raise a `ValueError` on the `.in_()` query
- Fix: removed `'investigating'` from `_ACTIVE_STATES`; active filter now correctly returns incidents in states: `open`, `in_progress`, `waiting_approval`, `approved`, `executing`, `awaiting_manual`, `storm_hold`
- Commit: `0991261`

### New Features ✨

**Watcher Metrics Dashboard**
- New real-time monitoring dashboard in the Admin panel showing live container CPU, memory, and disk utilisation
- Watcher self-registers and updates metrics every N seconds; multiple watcher hosts feed the same central pipeline
- Accurate disk reporting uses `df -B1` per container (v1.1.2+), replacing the previous Docker stats source

**Storm Incident Detail — Overview + AI Insights Tabs**
- Incident detail page for storm parent incidents redesigned with two tabs: Overview (timeline, affected resources) and AI Insights (LLM root-cause hypothesis with confidence score)

**Runbook Library UI**
- Searchable runbook catalogue with live confidence %, execution statistics bar (X/Y runs succeeded), trend badges (↑/↓/→), and in-UI editor

**CMDB Force Graph**
- Physics-based node layout for the CMDB topology view with HOSTED_ON/PART_OF traversal and live health overlay

### Documentation 📚

- `docs/Axiometica AIR Product_Capabilities.md` — new comprehensive capabilities reference; added multi-platform deployment coverage, platform-aware runbook selection with command variants, Connector Hub (9 certified adapters), and v1.1.2 UI features
- `README.md` — capabilities doc linked as primary entry point for new users; broken links removed
- `docs/FEATURES.md` — lifecycle_state values corrected; v1.1.2 features documented; Grafana and Generic connectors added
- `docs/WATCHER_TROUBLESHOOTING.md` — fixed incorrect health endpoint; added disk 0% troubleshooting guide
- `docs/QUICKSTART.md` — corrected directory names in shell commands; connector list updated
- All docs updated to v1.1.2

---

## [1.1.1] — 2026-06-02

### CMDB Live Data — Watcher Discovery & Metrics

**Watcher Discovery Now Fully Functional**
- Fixed `_run_discovery_via_api()` using undefined attributes (`self.api_url` → `self.api_base_url`, `self.api_key` → `self._api_headers`)
- Discovery was silently failing every poll cycle since initial deployment
- Container configuration (image, platform, CPU/memory limits, IPs, ports) now flows into Neo4j CMDB every N polls
- Backend confirms: "9/9 updated, 2 new" on first successful push after fix

**CMDB Node Detail Panel — Timestamp Formatting**
- "Updated" field previously showed `-13707s ago` (negative seconds — invalid)
- Root cause: `last_metrics_update` field not set by discovery; `timeAgo()` didn't handle negative/invalid dates
- Fixed: discovery now sets `ci.last_metrics_update = $last_discovered_at` in Neo4j
- Fixed: `timeAgo()` returns `"now"` for future/clock-skew dates, `"—"` for null/invalid
- Timestamps now display correctly: e.g. `"3m ago"`, `"1h ago"`

**User Account**
- Created missing admin account (was absent from `principals` table)
- Account verified functional with role-based access

### Script Fixes
- `scripts/test_platform.sh`: health check endpoint `/health` → `/api/health` (was returning 404)
- `scripts/test_watcher.sh`: health check `/health` → `/api/health`, incident endpoint `/workflows/incident` → `/api/workflows/incident`
- `start-all-services.ps1`: added `celery_default_worker` to Layer 3 startup sequence
- `start-all-services.sh`: added `celery_default_worker` to Layer 3 startup sequence; corrected service names `sentinel_senses` / `watcher_brain` → `sentinel` / `watcher`

### TypeScript Fix
- `RunbookEditor.tsx`: verification `StepList` was missing required `platform` prop (TypeScript compile error)

---

## [1.1.0] — 2026-06-02

### Major Feature: Platform-Aware Runbook Selection ✨

**Smart Deployment Platform Detection**
- Intelligent platform derivation: `graph-database` + `cmdb_platform='linux'` → infers Docker deployment
- Containerized service types (database, microservice, cache, etc.) automatically mapped to Docker
- Supports explicit platform declarations: docker | kubernetes | linux | windows | any
- Context serialization: Platform field now persists through entire workflow

**Platform-Aware Runbook Selection**
- MechanicAgent uses 4-pass cascade with platform awareness:
  - Pass 1: `event_type + service + platform` (most specific)
  - Pass 2: `event_type + service + 'any'` (service-specific, platform-agnostic)
  - Pass 3: `event_type + platform` (generic for platform)
  - Pass 4: `event_type + 'any'` (fully generic fallback)
- Runbooks ranked by success_rate DESC, then confidence DESC
- Example: "Kill Anomaly Process on Docker" selected for docker platform instead of generic runbook

**Chat API Platform-Aware Suggestions**
- Chat now suggests platform-specific runbooks when incident is open in UI
- Priority-based platform extraction: UI context > mentioned incidents
- Runbook RAG filters by platform: platform-specific + generic runbooks
- Chat respects deployment platform for better recommendations

### Enhanced Features 🚀

**Tool Platform Support**
- Tools now support `platforms` field: docker | kubernetes | linux | windows | any
- RunbookEditor filters tools by platform in dropdown
- Tool variants: platform-specific command implementations
- Example: `trace_syscalls` uses strace for linux, docker exec for docker

**Improved Tool Definitions**
- Fixed: Docker command placeholders using angle brackets → curly braces
- Fixed: Tool dropdown showing duplicate entries
- Fixed: Approval status visibility in tool dropdown
- Fixed: Arguments list clarity improvements

**Fixed Critical Bugs**
- Fixed: Platform field lost during context serialization
- Fixed: Missing logger import causing NameError in agents
- Fixed: CMDB OS platform overriding deployment platform inference
- Fixed: Context not propagating between LibrarianAgent → MechanicAgent

### Documentation 📚

- Created `docs/PLATFORM_SELECTION.md` — comprehensive guide to platform-aware selection
- Updated runbook design guide with platform considerations
- Created seed data reference: all tools, runbooks, actions with platform tags
- Updated README with platform-aware features

### Infrastructure 🏗️

- Updated Docker Compose files with latest configurations
- Created `install.sh` and `install.bat` with automated setup
- Added database migration scripts (run automatically on first deployment)
- Updated environment templates (`.env.example`)
- Created `platform_seed_data.py` with new runbooks for all platforms

---

## [1.0.5] — 2026-05-25

### Storm Agent — Phase 2 Enhancements

- **Neo4j dependency expansion** — after initial storm formation, a delayed sweep traverses the CMDB service graph to identify downstream incidents caused by the same root cause; qualifying incidents are adopted into the storm automatically
- **External connector storm eligibility** — per-connector `allow_storm_detection` flag (default: `true`) lets administrators exclude batch-sync sources from storm detection; the flag is stored as `storm_eligible` in each incident's context at ingest time
- **Bulk-sync false storm prevention** — storm detection window now uses `COALESCE(source_alert_time, created_at)` so that batch-synced alerts with old timestamps are evaluated against their original alert time, not the sync time
- **Global external exclusion setting** — new `storm.exclude_external_events` platform setting (default: `false`) allows all external connector incidents to be excluded from storm detection with a single toggle
- **Pipeline hold buffer** — new `storm.pipeline_hold_seconds` platform setting delays the incident processing pipeline after creation, giving storm detection time to cluster correlated events before individual pipelines run
- **Pre-pipeline storm guard** — if storm detection has already adopted an incident before its pipeline starts, the pipeline is skipped and the incident remains in `storm_hold`
- **Post-pipeline storm guard** — if an incident is adopted into a storm after its pipeline has partially executed, the lifecycle state is corrected to `storm_hold` and a warning note is written to the storm parent documenting the pre-storm remediations applied
- **Storm merge approval cancellation** — when a second storm detection task merges new incidents into an existing storm, any pending individual pipeline approvals for those incidents are automatically cancelled

### Bug Fixes

- **Storm parent lifecycle state** — storm parent incidents now correctly use `awaiting_manual` state (was incorrectly set to `waiting_approval`, which implied a standard pipeline approval gate)
- **ApprovalModel schema** — fixed invalid field names (`requires_cab`, `created_at`) used during CAB approval record creation for storm parents; corrected to `approval_type="cab"` and `requested_at`
- **Storm context shallow copy** — fixed a bug where updating `storm_analysis.affected_resources` in a shallow context copy mutated the original context dict, causing incorrect data to persist
- **UTC timestamp parsing** — `StormsDashboard` was parsing server timestamps without a `Z` suffix as local time, producing negative relative timestamps (e.g. `-13706s ago`); all timestamp parsing now appends `Z` for consistent UTC interpretation
- **Signal metric display** — zero-value signal metrics (`value=0`, `threshold=0`) are now hidden across `IncidentEventsPage`, `EventsFeed`, and `WorkflowDetailsPhase6` to avoid displaying meaningless `0 / 0 limit` subtitles for events that carry no real metric

### UI Improvements

- **Event Storms page** — replaced `⚡` emoji header with the standard Tabler `IconBolt` component (consistent with sidebar); Refresh button converted to outline style matching platform button standard
- **Storm action buttons** — Release and Resolve buttons now use the no-fill, colored-border style standard; button label simplified from "Release (dismiss storm)" to "Release"
- **Storm lifecycle badges** — `LifecycleBadge` component extended to handle `awaiting_manual` (orange) and `storm_hold` (violet) states with correct labels and colours
- **Real-time storm refresh** — `StormsDashboard` now subscribes to the WebSocket global event stream (`incident_created`, `incident_updated`) and immediately refreshes both the storm list and the selected detail panel; a separate 30-second interval poll also refreshes the detail panel (previously only the list polled)
- **Storm refresh interval stability** — fixed a dependency bug where selecting a different storm reset the 30-second list poll timer; `selectedId` is now tracked via a ref so the interval remains stable
- **Incident card left-border accent** — the coloured left border on `IncidentCard` is now state-aware: violet for `storm_hold`, orange for `awaiting_manual`, amber for `waiting_approval`, red for failed/rejected, green for resolved
- **Incident card source connector badge** — external-source incidents now display a "via `<connector>`" badge (cyan, `IconActivity` icon) in the card footer, matching the style used in the incident detail view
- **Lifecycle state filters** — `awaiting_manual` and `storm_hold` options added to the incident list filter dropdowns in both grid and table views; table view filter values corrected from uppercase to lowercase to match API enum values
- **`StatusBadge` component** — `storm_hold` added with violet colour (`#8b5cf6`) and label "Storm Hold"; `awaiting_manual` label confirmed as "Awaiting Manual"
- **`IncidentListTable` status badges** — `storm_hold` mapped to new `.status-purple` CSS class; `STATUS_LABEL` map extended to cover all lifecycle states including `storm_hold` and `awaiting_manual`
- **Signal metric formatting** — signal values and thresholds across all views now use `toLocaleString('en', { maximumFractionDigits: 1 })` for locale-aware formatting without unnecessary decimal places; label changed from "threshold" to "limit" for clarity

---

## [1.0.0] — 2026-05-24

Initial general availability release.

### Storm Agent — Correlated Event Detection

- **Storm Agent** — meta-orchestrator that detects correlated event bursts across multiple resources and suppresses individual remediations in favour of coordinated triage
- **Storm detection service** — lightweight SQL scan runs after every qualified event; triggers when ≥ 3 incidents span ≥ 2 resources within a configurable time window (default 120 s)
- **Storm parent incident** — single `waiting_approval` incident created to represent the full storm; child incidents placed in `storm_hold` state, suppressing their individual pipelines
- **Storm merge logic** — concurrent Celery tasks that detect overlapping incident sets adopt remaining children into the same parent rather than creating duplicate storms
- **Multi-source correlation** — storm detection works identically across watcher events and Splunk webhook alerts; the event source is normalised before detection runs
- **LLM root cause hypothesis** — the configured LLM provider generates a natural-language hypothesis from affected resources, event types, and Neo4j topology
- **Neo4j topology analysis** — shared upstream CIs found in the CMDB dependency graph are surfaced as ranked root cause candidates with confidence scores
- **Pattern classification** — four deterministic patterns: `network_partition`, `resource_exhaustion`, `service_cascade`, `mixed_signal_storm`
- **Storm Actions API** — `POST /api/storms/{id}/release` (dismiss, return children to individual pipelines) and `POST /api/storms/{id}/resolve` (bulk-close all children)
- **Storm settings** — all detection and behaviour parameters configurable at runtime via `GET/PUT /api/settings/storm`: window, thresholds, CAB requirement, auto-hold, LLM toggle, Neo4j toggle
- **Event Storms UI** — dedicated sidebar page showing active storms, hypothesis text, root cause candidates, child incident table, and Release / Resolve action buttons
- **Storm simulation script** — `scripts/simulate_event_storm.py` fires a two-wave, six-event scenario across both watcher and Splunk sources to validate storm detection end-to-end

### Core Platform

- **Multi-agent AI incident pipeline** — seven specialised AI agents orchestrate the full incident lifecycle autonomously:
  `SentinelAgent` → `LibrarianAgent` → `RiskAssessor` → `MechanicAgent` → `PolicyBrokerAgent` → `ToolRegistryAgent` → `VerifierAgent`
- **Real-time incident detection** — kernel-level eBPF syscall monitoring, container CPU/memory/disk/network anomaly detection, HTTP/TCP health checks
- **Incident deduplication** — duplicate alerts on the same resource and event type update the existing incident rather than creating noise
- **Automated remediation** — runbook-driven remediation with configurable strategies per anomaly type (process kill, service restart, resource scaling)
- **Intelligent process identification** — three-layer detection (PID=1, PPID=1, binary-name matching) ensures the correct container process is targeted
- **Asynchronous pipeline** — all agent work runs in Celery; the API returns immediately and pushes progress via WebSocket
- **Watcher all-clear** — automated recovery detection closes incidents when the monitored condition self-resolves, without human intervention

### Governance & Approval

- **CAB approval workflow** — incidents exceeding risk thresholds or matching policy rules pause for human approval before remediation executes
- **Policy engine** — flexible rule set controls which incidents auto-remediate vs. require approval, based on anomaly type, severity, risk score, and resource pattern
- **Risk scoring** — 0–100 composite risk score derived from severity, CMDB criticality, blast-radius, and SPOF status
- **Governance audit trail** — every approval decision (who, when, notes) is recorded and surfaced in the incident timeline

### Incident Management UI

- **Dashboard** — live metrics cards (open incidents, pending approvals, auto-remediated today, average risk score) with real-time WebSocket updates
- **Incident list** — grid and table views with server-side pagination, column sorting, and filtering by lifecycle state and severity
- **Incident detail** — full lifecycle timeline, reasoning trace, remediation outcome, resolution source, and CMDB context
- **Work Notes** — collaborative incident thread with four note types (note, action, escalation, system) for operator communication
- **Approval queue** — dedicated queue page for reviewing and deciding on pending remediations
- **Operator action controls** — when automation stalls (`awaiting_manual`), in-context buttons to retry automation, add a work note, or resolve manually
- **Incident numbering** — sequential INC0001-style identifiers for every incident
- **Duration tracking** — live elapsed-time counter for open incidents; stable time-to-resolve for closed ones

### CMDB & Integrations

- **Neo4j service graph** — service topology, dependency mapping, and environment classification stored as a graph database
- **ServiceNow CMDB connector** — pull CI records, operational status, and relationships from ServiceNow into the local cache; browse via CMDB Browser UI
- **Splunk connector** — pull logs from Splunk for log-analysis steps in runbooks
- **Alert ingest connectors** — receive webhook alerts from Datadog, Dynatrace, Prometheus, PagerDuty, and Zabbix
- **Connector Hub** — central configuration UI for all external integrations with test, sync, and status visibility

### Runbook Engine

- **YAML-defined runbooks** — step-by-step remediation procedures with conditional execution (`run_if`), failure policies (`on_failure: abort`), and tool routing
- **Runbook library** — pre-built runbooks for high CPU, high memory, disk full, service unresponsive, and network anomaly
- **Runbook browser** — read-only UI for operators to review available runbooks and their steps
- **Runbook management** — full CRUD API and admin UI for creating and editing runbooks

### Administration

- **User management** — role-based access control with four roles: Admin, ITOM Admin, Operator, Viewer
- **Admin panel** — system statistics, database health, and bulk incident management
- **Platform settings** — configurable watcher thresholds (CPU %, memory %, disk %, cooldown, poll interval) and Storm Agent parameters via the UI without code changes
- **Audit log** — all user and system actions recorded with timestamp and actor

### Infrastructure

- **Docker Compose stack** — nine containerised services: backend (FastAPI), celery_worker, postgres, redis, neo4j, flower, frontend, sentinel_senses (eBPF), watcher_brain
- **One-command install** — `install.sh` (Linux/macOS) and `install.bat` (Windows) handle prerequisites, image build, migrations, seeding, and CMDB initialisation
- **Idempotent setup** — `setup_oob.py` is safe to re-run; all seed operations use `ON CONFLICT DO NOTHING`
- **Health checks** — all services expose health endpoints; Docker Compose dependency ordering waits for readiness

---

[Unreleased]: https://github.com/axiometica/axiometica-air/compare/v1.2.0...HEAD
[1.3.0]: https://github.com/axiometica/axiometica-air/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/axiometica/axiometica-air/compare/v1.1.2...v1.2.0
[1.1.2]: https://github.com/axiometica/axiometica-air/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/axiometica/axiometica-air/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/axiometica/axiometica-air/compare/v1.0.5...v1.1.0
[1.0.5]: https://github.com/axiometica/axiometica-air/compare/v1.0.0...v1.0.5
[1.0.0]: https://github.com/axiometica/axiometica-air/releases/tag/v1.0.0
