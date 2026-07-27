"""
Approved Actions catalog — CRUD endpoints.
GET    /api/approved-actions                   list all (optional ?category=)
GET    /api/approved-actions/{id}              get one
POST   /api/approved-actions                   create custom action
PUT    /api/approved-actions/{id}              update (rules, enabled, etc.)
DELETE /api/approved-actions/{id}              delete custom action
POST   /api/approved-actions/validate-process  validate process name against rules
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any
from uuid import UUID
import re
import json

from agentic_os.db.database import get_session
from agentic_os.db.repositories import ApprovedActionRepository

router = APIRouter(prefix="/approved-actions", tags=["approved-actions"])


def _strip_md_fences(s: str) -> str:
    # Some models wrap JSON responses in ```json ... ``` fences even when asked
    # to reply with raw JSON. Strip a single leading and trailing fence, if any.
    s = re.sub(r'^```(?:json)?\s*', '', s.strip())
    return re.sub(r'\s*```$', '', s)


# ─── Request / Response schemas ────────────────────────────────────────────────

class ProcessRule(BaseModel):
    priority:    int
    allow:       bool
    pattern:     str
    description: str = ""


class ActionCreateRequest(BaseModel):
    tool_name:         str
    name:              str
    description:       Optional[str] = ""
    command:           Optional[str] = None   # default/fallback command
    command_variants:  Optional[dict] = None  # {"docker": "...", "kubernetes": "...", "ssh": "...", "any": "..."}
    category:          str           # diagnostic | remediation_safe | remediation_intrusive
    blast_radius:      int = 1
    requires_approval: bool = False
    enabled:           bool = True
    parameters:        List[Any] = []
    process_rules:     Optional[List[Any]] = None
    output_fields:     List[Any] = []


class ActionUpdateRequest(BaseModel):
    name:              Optional[str]       = None
    description:       Optional[str]       = None
    command:           Optional[str]       = None
    command_variants:  Optional[dict]      = None  # null = keep existing; {} = clear all variants
    blast_radius:      Optional[int]       = None
    requires_approval: Optional[bool]      = None
    enabled:           Optional[bool]      = None
    parameters:        Optional[List[Any]] = None
    process_rules:     Optional[List[Any]] = None   # null = keep existing; [] = clear rules
    output_fields:     Optional[List[Any]] = None   # null = keep existing; [] = clear — rejected for built-in tools


class ProcessValidateRequest(BaseModel):
    tool_name:    str
    process_name: str


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_actions(
    category: Optional[str] = None,
    enabled_only: bool = False,
    db: Session = Depends(get_session),
):
    repo  = ApprovedActionRepository(db)
    items = repo.list(category=category, enabled_only=enabled_only)
    return [repo.to_dict(a) for a in items]


class ValidateCommandRequest(BaseModel):
    command: str


@router.post("/validate-command")
def validate_arbitrary_command(body: ValidateCommandRequest):
    """
    Validate any command string on demand — used by the Tool Builder Review
    step and the Action Editor's "Validate" button to check un-saved edits
    without needing to write to the DB first.

    Returns a single ValidationResult as a dict — see command_validator.py.
    """
    from agentic_os.services.command_validator import validate_command
    return validate_command(body.command).as_dict()


@router.get("/{action_id}/validate")
def validate_single_action(action_id: UUID, db: Session = Depends(get_session)):
    """
    Validate one action's command + every command_variant. Returns a map
    keyed by adapter ("command", "docker", "ssh", …) with per-variant
    {ok, stage, message} — same shape as the per-item entries in
    /validate-all's issues list, plus a top-level "ok" summary.
    """
    from agentic_os.services.command_validator import validate_action
    repo   = ApprovedActionRepository(db)
    action = repo.get(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    results = validate_action(action)
    return {
        "action_id": str(action.id),
        "tool_name": action.tool_name,
        "name":      action.name,
        "ok":        all(v.ok for v in results.values()),
        "results":   {k: v.as_dict() for k, v in results.items()},
    }


@router.get("/validate-all")
def validate_all_actions(db: Session = Depends(get_session)):
    """
    Sweep every approved_action's command + command_variants through the
    shared shell-syntax validator. Returns a report of broken variants so
    operators can locate LLM-generated or hand-authored tools that will
    silently fail at runtime.

    Response shape:
      {
        "checked": <int>,      # total actions inspected
        "ok":      <int>,      # actions with all variants passing
        "broken":  <int>,      # actions with at least one failing variant
        "issues":  [
          {
            "action_id":   "<uuid>",
            "tool_name":   "…",
            "name":        "Human name",
            "failures": {
              "<adapter>": {"ok": false, "stage": "inner_script", "message": "…"},
              …
            }
          },
          …
        ]
      }
    """
    from agentic_os.services.command_validator import validate_action

    repo  = ApprovedActionRepository(db)
    items = repo.list()

    checked = 0
    ok      = 0
    issues: list[dict] = []

    for a in items:
        checked += 1
        results  = validate_action(a)
        failures = {k: v.as_dict() for k, v in results.items() if not v.ok}
        if failures:
            issues.append({
                "action_id": str(a.id),
                "tool_name": a.tool_name,
                "name":      a.name,
                "failures":  failures,
            })
        else:
            ok += 1

    return {
        "checked": checked,
        "ok":      ok,
        "broken":  len(issues),
        "issues":  issues,
    }


@router.get("/{action_id}")
def get_action(action_id: UUID, db: Session = Depends(get_session)):
    repo   = ApprovedActionRepository(db)
    action = repo.get(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return repo.to_dict(action)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_action(body: ActionCreateRequest, db: Session = Depends(get_session)):
    repo = ApprovedActionRepository(db)
    existing = repo.get_by_tool_name(body.tool_name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Action with tool_name '{body.tool_name}' already exists",
        )
    action = repo.create(body.model_dump())
    return repo.to_dict(action)


@router.put("/{action_id}")
def update_action(
    action_id: UUID,
    body: ActionUpdateRequest,
    db: Session = Depends(get_session),
):
    repo = ApprovedActionRepository(db)
    existing = repo.get(action_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Action not found")
    if body.output_fields is not None and existing.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="output_fields is locked for built-in tools and cannot be edited",
        )

    data = {k: v for k, v in body.model_dump().items() if v is not None}
    # Allow explicitly setting process_rules/command_variants to empty collection
    if body.process_rules is not None:
        data["process_rules"] = body.process_rules
    if body.command_variants is not None:
        data["command_variants"] = body.command_variants
    updated = repo.update(action_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Action not found")
    return repo.to_dict(updated)


@router.delete("/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_action(action_id: UUID, db: Session = Depends(get_session)):
    repo = ApprovedActionRepository(db)
    existing = repo.get(action_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Action not found")
    if existing.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in actions cannot be deleted. Disable the action instead.",
        )
    if not repo.delete(action_id):
        raise HTTPException(status_code=404, detail="Action not found")


class GenerateToolRequest(BaseModel):
    description: str
    adapter_hints: Optional[List[str]] = None


class ParseOutputRequest(BaseModel):
    tool_name:    str
    sample_output: str
    command:      Optional[str] = None


@router.post("/generate")
async def generate_tool_definition(body: GenerateToolRequest):
    """
    Use the platform LLM to draft a complete approved-action catalog entry from a
    plain-English description. Returns a JSON object ready for POST /approved-actions.
    """
    from agentic_os.services.summary_service import get_summary_service

    provider = get_summary_service().provider
    if not provider.is_configured():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="LLM not configured — go to Settings → LLM to set up a provider first.",
        )

    adapter_hint_text = ""
    if body.adapter_hints:
        adapter_hint_text = f"\nFocus especially on these adapters: {', '.join(body.adapter_hints)}."

    system_prompt = (
        "You are an expert DevOps engineer generating tool catalog entries for an IT "
        "automation platform. Each tool is a shell command the platform sends to a watcher "
        "agent running on the target system. The watcher selects the correct command_variant "
        "based on its adapter type and executes it directly.\n\n"
        "CRITICAL adapter command conventions:\n"
        "- docker:     Use 'docker exec {target} bash -c '<cmd>'' where {target} is the "
        "auto-injected container name from the watcher context. "
        "For chained commands wrap them ALL inside ONE bash -c '...' — a single docker exec "
        "call, never one docker exec per sub-command. NEVER nest docker exec inside another "
        "docker exec. NEVER use {{container_name}} — use {target} instead (single braces, "
        "auto-injected, no manual entry needed).\n"
        "- ssh:        bare shell command, runs on the remote host directly.\n"
        "- kubernetes: ALWAYS prefix with 'kubectl exec -n {{namespace}} {{pod_name}} -- <cmd>' "
        "to run inside a pod, OR use kubectl CLI commands (kubectl get, kubectl describe, etc.).\n"
        "- aws_ssm:    bare shell command, delivered via SSM Run Command to an EC2 instance.\n"
        "- azure:      bare shell command, delivered via Azure Run Command to a VM.\n"
        "- any:        fallback bare shell command for unrecognised adapters.\n\n"
        "Use {{param_name}} placeholders for runtime values (double braces = user-supplied). "
        "Use {param_name} (single braces) ONLY for platform-injected values: {target}.\n"
        "Respond with ONLY valid JSON — no markdown fences, no extra text.\n\n"
        "Common command conventions:\n"
        "- key=value output: ALL fields must use 'echo \"key=$(command)\"' format — "
        "NEVER output a bare unformatted value. This applies to every field including hostname: "
        "always 'echo \"hostname=$(hostname)\"', never just 'hostname'.\n"
        "- uptime: use 'cut -d. -f1 /proc/uptime' to get a clean integer (not awk which "
        "returns a float like 30052.18).\n"
        "- chained key=value commands: connect with && inside a single bash -c '...' so "
        "all fields are collected in one exec call.\n"
        "- curl HTTP checks: always use -s -o /dev/null -w \"%{http_code} %{time_total}\\n\" "
        "to produce a single space-separated line. NEVER use JSON format strings or "
        "${if_eq:...} constructs — those do not exist in curl. "
        "Capture http_code and time_total as separate output fields; do NOT add an "
        "is_healthy field (the runbook can derive that from the status code).\n"
        "- ps / top / ss / netstat: always pipe through grep/awk or wc -l to reduce "
        "to a single-value output rather than returning a raw table.\n\n"
        "CRITICAL — shell quoting (this is where most tools break):\n"
        "You are writing bash commands wrapped in an outer single-quoted string "
        "(bash -c '...'). Any embedded awk/sed/perl/jq/python -c script also needs its "
        "own single quotes around its program. bash single-quoted strings do NOT process "
        "escape sequences — inside 'like this', a backslash is a literal backslash. "
        "So \\' does NOT insert a single quote; it inserts a literal backslash followed by "
        "closing the string. Do NOT use \\' inside single-quoted strings.\n"
        "The ONLY correct way to embed a single quote inside a single-quoted string is "
        "the four-character sequence '\\'' — that closes the string, inserts a literal "
        "single quote via backslash-escape (which works because you're briefly out of "
        "quotes), and reopens the string.\n"
        "  WRONG:  bash -c 'awk \\'$6>=100 {print $1}\\''   -> literally passes \\ to awk\n"
        "  RIGHT:  bash -c 'awk '\\''$6>=100 {print $1}'\\'''\n"
        "IRON RULE — NEVER NEST SAME-TYPE QUOTES.\n"
        "If the outer wrapper is double-quoted (\"...\"), every inner quoted region MUST use "
        "single quotes ('...'). If the outer wrapper is single-quoted ('...'), every inner "
        "quoted region MUST use double quotes or the '\\'' idiom. Nesting the same quote type "
        "silently closes the outer string mid-command and everything after runs in a broken "
        "shell context.\n"
        "  WRONG:  bash -c \"ps aux | awk \"\\$6>=100 {print \\$1}\"\"\n"
        "          (inner \" closes the outer bash -c \"...\" early — awk gets $6/$1 as\n"
        "          empty shell vars and > becomes shell redirection)\n"
        "  RIGHT:  bash -c \"ps aux | awk '\\$6>=100 {print \\$1}'\"\n"
        "          (inner single quotes are fine inside double quotes; \\$ stays escaped so\n"
        "          awk sees literal $6/$1)\n"
        "STRONGLY PREFERRED — avoid the escape hell entirely with any of these patterns:\n"
        "  1. awk -v: pass shell values into awk as awk variables, no embedded quotes.\n"
        "     bash -c 'awk -v thresh=100 \"\\$6>=thresh {print \\$1}\" <(ps aux)'\n"
        "  2. Split the awk program into a script that uses NO shell interpolation, then\n"
        "     wrap the WHOLE bash -c body in DOUBLE quotes with the awk program in single.\n"
        "     bash -c \"ps aux | awk '\\$6>=100 {print \\$1}'\"\n"
        "  3. When awk/sed only needs a fixed regex or literal, put it in single quotes:\n"
        "     ps aux | grep -v grep | awk '/nginx/ {print $2}'\n"
        "In general: if you find yourself writing three or more quote characters in a row, "
        "stop and rewrite using awk -v, double-quoted outer wrapper, or splitting the pipeline.\n\n"
        "IRON RULE — SQL LITERALS INSIDE bash -c ARE A LANDMINE.\n"
        "SQL requires string values to be wrapped in SINGLE quotes: state='active', "
        "role='admin', name='foo'. When that SQL is embedded inside a bash -c '...' outer "
        "wrapper, those inner single quotes CLOSE THE OUTER STRING and every character "
        "after runs in a different shell context. This produces a bash-valid command "
        "whose SQL has NO string literal quotes — the DB then complains that 'active' "
        "is an unknown column. The shell-syntax validator will NOT catch this because "
        "bash is happy; only the DB engine will reject the query at runtime.\n"
        "This applies to EVERY SQL / query CLI: psql, mysql, mysqladmin, mariadb, mongo, "
        "mongosh, redis-cli, sqlite3, cqlsh, clickhouse-client, influx, bq.\n"
        "  WRONG:  bash -c 'psql -c \"SELECT * FROM t WHERE state='active'\"'\n"
        "          (the SQL 'active' quotes close the outer bash string — reaches DB as\n"
        "          `SELECT * FROM t WHERE state=active` → column-not-found error)\n"
        "  RIGHT:  bash -c \"psql -c \\\"SELECT * FROM t WHERE state='active'\\\"\"\n"
        "          (outer double, inner double escaped — SQL single quotes stay intact)\n"
        "  ALSO RIGHT:  bash -c 'psql -c \"SELECT * FROM t WHERE state='\\''active'\\''\"'\n"
        "          (outer single, SQL literal quotes escaped with the '\\'' idiom)\n"
        "STRONGLY PREFERRED — sidestep the quoting entirely:\n"
        "  1. Wrap the outer bash -c in DOUBLE quotes so inner SQL single quotes are "
        "just characters. Escape the wrapping -c argument's own double quotes as \\\".\n"
        "     docker exec {target} bash -c \"psql -U postgres -c \\\"SELECT ... WHERE x='y'\\\"\"\n"
        "  2. Pass the query on stdin with a heredoc — no quoting collision:\n"
        "     docker exec {target} bash -c \"psql -U postgres <<'SQL'\\nSELECT ... WHERE x='y';\\nSQL\"\n"
        "  3. For a single value comparison, use psql / mysql variable binding:\n"
        "     docker exec {target} psql -U postgres -v state=active -c \"SELECT ... WHERE state = :'state'\"\n"
        "Prefer pattern 1 (outer double + escaped inner double) — it is the shortest, "
        "reads naturally, and the shell-syntax validator can verify it end-to-end.\n"
        "One more gotcha specific to SQL clients: NEVER wrap the whole invocation in "
        "'echo \"key=$(psql ...)\"' — it swallows non-zero exit codes and hides DB "
        "errors from the runbook. Call the client directly; the platform captures stdout "
        "and the exit code separately."
    )

    user_prompt = f"""Generate a complete tool catalog entry for this request:

{body.description}{adapter_hint_text}

Return a JSON object with exactly these fields:
{{
  "tool_name": "snake_case_unique_identifier",
  "name": "Human-Readable Name",
  "description": "1-2 sentence description of what this tool does",
  "command_variants": {{
    "docker":     "docker exec {{{{container_name}}}} <bare-command>  (or null if N/A)",
    "ssh":        "<bare-command>  (or null if N/A)",
    "kubernetes": "kubectl exec -n {{{{namespace}}}} {{{{pod_name}}}} -- <bare-command>  (or null if N/A)",
    "vcenter":    null,
    "aws_ssm":    "<bare-command>  (or null if N/A)",
    "azure":      "<bare-command>  (or null if N/A)",
    "any":        "<bare-command fallback>  (or null if truly adapter-specific)"
  }},
  "category": "diagnostic | remediation_safe | remediation_intrusive",
  "blast_radius": 1,
  "requires_approval": false,
  "enabled": false,
  "parameters": [
    {{"name": "param_name", "type": "string|number|boolean", "required": false, "description": "what it is and which adapter needs it", "default": null}}
  ],
  "output_fields": [
    {{"field": "snake_case_field", "description": "what this value represents", "type": "string"}}
  ]
}}

Rules:
- blast_radius: 1=read-only, 2=safe change, 3=service impact, 4=data risk, 5=destructive
- requires_approval should be true for blast_radius >= 3
- infer ALL parameters from {{{{placeholders}}}} used in command_variants — include container_name, namespace, pod_name if those prefixes are used
- parameter required field: adapter-scoped params (container_name, namespace, pod_name) are ALWAYS required=false because they only apply to one adapter and are injected automatically by the platform from the watcher's registration context; only mark required=true for params the operator must supply explicitly (e.g. a hostname to ping, a process name to kill)
- output_fields: ONLY populate this if the command has fixed, predictable output format that
  you can verify from the research sample. List every useful individual value a downstream
  runbook step might need — counts, names, statuses, IPs, PIDs, program names.
  If the command output depends on runtime input (e.g. a user-supplied script path, a filename,
  a query string) or cannot be predicted without knowing that input, set output_fields to [].
  An empty output_fields is correct and expected in those cases — generating speculative fields
  that don't match real output is worse than leaving it empty. The operator will use the Refine
  section to populate fields from real output after running the tool.
- IMPORTANT — tabular / multi-row output: if the command produces a table with many rows
  (e.g. ss, netstat, ps, df, docker ps), the platform captures ONE value per field.
  Handle this with ONE of these patterns:
    a) Parameterise + filter: add a parameter (e.g. {{port}}) and pipe the command through grep/awk
       so the output is 0–1 matching lines. Good for "check if X is present" queries.
    b) Aggregate: pipe through "| wc -l" or "| grep -c <pattern>" to produce a single count.
       Add a field named "<noun>_count" with type "integer". Good for "how many X" queries.
    c) Both: if the description asks for a count AND a specific check, generate two fields.
  If the description says "list all" with no filter/count intent, still add a "_count" summary
  field so runbooks can threshold on it, and note the limitation in the tool description.
- use null only for adapters where the command genuinely cannot apply
- do NOT include pattern or kind in output_fields — those are generated in a separate step"""

    def _build_pattern(strategy: str, col: int = 0, split_char: str = "/",
                       literal: str = "", type_hint: str = "string") -> str:
        """Build a regex from a location strategy — no LLM regex writing needed."""
        num = r"\d+" if type_hint == "number" else r"\S+"
        if strategy == "column" and col >= 1:
            # ^\s* handles ps-style right-aligned output with leading spaces
            prefix = r"^\s*" + r"\S+\s+" * (col - 1)
            return prefix + r"(\S+)"
        if strategy == "tail_of_line" and col >= 1:
            # Capture everything from column N to end of line (multi-word fields)
            prefix = r"^\s*" + r"\S+\s+" * (col - 1)
            return prefix + r"(.+?)\s*$"
        if strategy == "end_split_before":
            inner = r"\d+" if type_hint == "number" else r"\S+?"
            return f"({inner}){re.escape(split_char)}\\S+\\s*$"
        if strategy == "end_split_after":
            return f"\\S+{re.escape(split_char)}(\\S+)\\s*$"
        if strategy == "last_column":
            return f"({num})\\s*$"
        if strategy == "after_literal" and literal:
            # Use [\d.]+ for numbers so trailing commas/units are excluded
            inner = r"[\d.]+" if type_hint == "number" else r"\S+"
            return f"{re.escape(literal)}\\s*({inner})"
        if strategy == "single_value":
            # Entire (trimmed) line is the value
            return f"^\\s*({num})\\s*$"
        return ""

    # Fields whose values can contain spaces — auto-upgraded from column → tail_of_line
    MULTIWORD_HINTS = {
        "command", "cmd", "cmdline", "message", "msg", "log", "entry",
        "description", "path", "filename", "arguments", "args", "text",
    }

    async def _research(provider, description: str) -> dict:
        """Call 0: research the correct bare command and produce realistic sample output.
        Returns {"command": str, "sample_output": [str, ...]}."""
        raw = await provider.generate_agent_completion(
            system_prompt=(
                "You are a senior DevOps/SRE engineer with deep knowledge of Linux, "
                "containers, databases, and monitoring tools. "
                "Respond with ONLY valid JSON — no markdown fences, no extra text."
            ),
            user_content=f"""Task: {description}

Step 1 — identify the single best bare shell command (or short pipeline) to accomplish this.
Step 2 — assess whether the command has FIXED, PREDICTABLE output format:
         - Fixed format: the output always has the same columns/structure (e.g. free -m, df -h, ps aux)
         - Variable format: the output depends on a user-supplied parameter such as a script path,
           filename, query, or other runtime input — you cannot know the format in advance.
         If fixed: write 8-10 realistic sample output lines the command would produce.
         If variable: set sample_output to [] — do not invent output.

Return JSON:
{{
  "command": "bare shell command without docker/kubectl wrappers",
  "output_predictable": true,
  "sample_output": [
    "line1",
    "line2"
  ]
}}

Rules:
- command must NOT include 'docker exec', 'kubectl exec', or adapter wrappers
- sample_output must reflect what that exact command would print — format, columns, labels
- Use realistic values; headers should match real command output
- If output_predictable is false, sample_output MUST be [] — never invent sample lines""",
            max_tokens=800,
            temperature=0.2,
        )
        cleaned = re.sub(r'\br"((?:[^"\\]|\\.)*)"', r'"\1"', _strip_md_fences(raw))
        return json.loads(cleaned)

    async def _generate_patterns(provider, command: str, fields: list,
                                  known_sample_lines: list | None = None) -> dict:
        """Call 2: LLM locates each value (col# or split strategy),
        Python builds the regex — no LLM regex writing.
        If known_sample_lines are provided (from call 0 research), they are used
        directly instead of asking the LLM to generate its own."""
        def _is_count_field(f: dict, cmd: str = "") -> bool:
            """True only when the command produces RAW multi-row tabular output and the
            field counts rows in it. NOT for fields whose value is already a scalar
            (e.g. produced by piping through wc -l or grep -c)."""
            name = f.get("field", "")
            if not (name.endswith("_count") or name in ("count", "total", "num_connections", "num_processes")):
                return False
            # If the command already aggregates to a single value, the field is
            # a scalar — use regex extraction, not runtime line-counting.
            if re.search(r'\|\s*(?:wc\s+-l|grep\s+-c)', cmd):
                return False
            return True

        field_template = json.dumps(
            [{"field": f["field"],
              "description": f.get("description", ""),
              "strategy": (
                  "count"
                  if _is_count_field(f, command)
                  else "<FILL: single_value | column | after_literal | end_split_before | end_split_after | last_column | count>"
              ),
              "col": "<if column: 1-based column number, else omit>",
              "split_char": "<if end_split_before/after: single delimiter char, else omit>",
              "literal": "<if after_literal: exact substring immediately before the value, else omit>",
              "match_pattern": "<if count: optional regex to filter which lines to count; omit to count all non-empty lines>",
              "type": f.get("type", "string")}
             for f in fields],
            indent=4
        )
        pattern_system = (
            "You analyse shell command output and identify where each field value appears. "
            "Respond with ONLY valid JSON — no markdown fences, no extra text."
        )

        if known_sample_lines:
            # Use researched sample — skip the "write sample lines" step
            sample_block = "\n".join(known_sample_lines[:10])
            sample_instruction = f"Sample output (authoritative — analyse these lines, do NOT rewrite them):\n{sample_block}"
            col_map_instruction = "List every whitespace-delimited token in the first data row (skip header if present) with its 1-based column number."
            sample_json_key = ""  # caller injects known_sample_lines after parsing
        else:
            sample_instruction = "Step 1 — write 2 realistic sample data rows (NOT headers)."
            col_map_instruction = "List every whitespace-delimited token in sample_lines[0] with its 1-based column number."
            sample_json_key = '"sample_lines": ["<row1>", "<row2>"],'

        pattern_prompt = f"""Command: {command}

{sample_instruction}

{col_map_instruction}
Example: "tcp 0 0 0.0.0.0:22 0.0.0.0:* LISTEN 1234/sshd"
  → col1=tcp  col2=0  col3=0  col4=0.0.0.0:22  col5=0.0.0.0:*  col6=LISTEN  col7=1234/sshd

Pick ONE strategy per field:

  single_value     — ENTIRE line is one bare value (e.g. just "42" or "OK")
  column           — value is exactly one whitespace-delimited token; provide col=N
                     Use this for IPs, ports, names, numbers, percentages — anything that is a single token
  after_literal    — value follows a fixed label/marker in the line
                     e.g. "load average: 0.45" → literal="average:", captures "0.45"
                     e.g. "used_memory:1048576" → literal="used_memory:", captures "1048576"
  end_split_before — value is BEFORE split_char in the last token (e.g. "1234" from "1234/sshd")
  end_split_after  — value is AFTER split_char in the last token  (e.g. "sshd" from "1234/sshd")
  last_column      — value IS the entire last token
  count            — COUNT how many lines match a pattern; for fields named *_count or of type integer
                     when the command output is tabular (many rows).
                     Provide "match_pattern": a simple regex to match relevant lines (e.g. "LISTEN" to
                     count listening ports). Omit match_pattern to count all non-empty data lines.
                     Do NOT use a capture group — just a match filter.

IMPORTANT: always prefer "column" for single-token values.
Use "count" for any field whose name ends with "_count" or that counts rows in tabular output.

COLUMN MATCHING RULE: if the command output has a header row, first identify which header
label semantically matches each output field, then use THAT column's 1-based position.
Example: if headers are "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port"
  then field "state" → header "State" → col=2 (NOT col=1 which is "Netid")
  and  field "local_address" → header "Local Address:Port" → col=5

Return JSON:
{{
  {sample_json_key}
  "col_map": "<col1=X col2=Y …>",
  "output_fields": {field_template}
}}

Rules:
- col is a plain integer; literal is a string; split_char is a single character
- omit keys not needed by the chosen strategy
- for "count" strategy: include "match_pattern" with a filter regex (no capture group) or omit it to count all lines
- do NOT add a "pattern" key"""

        raw = await provider.generate_agent_completion(
            system_prompt=pattern_system,
            user_content=pattern_prompt,
            max_tokens=1200,
            temperature=0.1,
        )
        cleaned = re.sub(r'\br"((?:[^"\\]|\\.)*)"', r'"\1"', _strip_md_fences(raw))
        data = json.loads(cleaned)

        # If we already have authoritative sample lines, inject them
        if known_sample_lines:
            data["sample_lines"] = known_sample_lines

        def _is_header(line: str) -> bool:
            """True if the line looks like a column header rather than data.
            Headers typically contain no digit sequences (Netid, State, Proto,
            CONTAINER, NAME …). Data lines almost always have at least one digit
            (a PID, port, byte count, IP address, etc.)."""
            stripped = line.strip()
            if not stripped:
                return True
            if not any(ch.isdigit() for ch in stripped):
                return True
            # All-caps first token is a strong header signal (CONTAINER, IMAGE …)
            first = stripped.split()[0]
            if first.isupper() and first.isalpha() and len(first) > 2:
                return True
            return False

        # Build and validate patterns from location info
        sample_lines = data.get("sample_lines", [])
        # Prefer data lines (non-headers) for validation so a header row can't
        # accidentally satisfy a pattern intended for real data.
        data_lines = [l for l in sample_lines if not _is_header(l)] or sample_lines

        for field in data.get("output_fields", []):
            strategy      = field.pop("strategy", "")
            col           = int(field.pop("col", 0)) if "col" in field else 0
            split_ch      = field.pop("split_char", "/")
            literal       = field.pop("literal", "")
            match_pattern = field.pop("match_pattern", "")
            type_hint     = field.get("type", "string")

            # count strategy — count matching lines at runtime rather than capturing a value
            if strategy == "count":
                field["kind"] = "count"
                if match_pattern:
                    try:
                        re.compile(match_pattern)
                        field["pattern"] = match_pattern
                    except re.error:
                        field["pattern"] = ""
                else:
                    field["pattern"] = ""
                continue

            # Auto-upgrade column → tail_of_line for fields whose name/description
            # hints at multi-word content (command lines, log messages, paths, etc.)
            if strategy == "column" and col >= 1:
                field_tokens = (field.get("field", "") + " " + field.get("description", "")).lower().split()
                if any(h in field_tokens for h in MULTIWORD_HINTS):
                    strategy = "tail_of_line"

            pat = _build_pattern(strategy, col, split_ch, literal, type_hint)
            field["kind"] = "regex"

            if pat and data_lines:
                try:
                    # Accept if pattern matches any data line (header lines excluded)
                    matched = any(
                        (m := re.search(pat, line)) and m.group(1)
                        for line in data_lines
                    )
                    field["pattern"] = pat if matched else ""
                except re.error:
                    field["pattern"] = ""
            else:
                field["pattern"] = pat

        return data

    def _sanitize_tool_def(td: dict) -> dict:
        """
        Deterministic post-processing of Call 1 output.
        Fixes known LLM failure patterns so Call 2 works against clean data
        and the registered tool runs correctly without manual edits.
        """
        variants: dict = td.get("command_variants") or {}

        # ── Docker-specific fixes ─────────────────────────────────────────────
        docker_cmd = variants.get("docker") or ""
        if docker_cmd:
            # 1. Normalise container reference: {{container_name}} → {target}
            docker_cmd = re.sub(r'\{\{container_name\}\}', '{target}', docker_cmd)

            # 2. Remove double-wrapping: docker exec {target} sh -c "docker exec {target} ..."
            #    Keep only the inner command string.
            double_wrap = re.match(
                r'''docker\s+exec\s+\{target\}\s+(?:sh|bash)\s+-c\s+["']docker\s+exec\s+\{target\}\s+(.+)["']''',
                docker_cmd, re.DOTALL
            )
            if double_wrap:
                docker_cmd = f"docker exec {{target}} bash -c '{double_wrap.group(1)}'"

            # 3. Consolidate: multiple "docker exec {target} <cmd>" calls chained with &&
            #    → single "docker exec {target} bash -c '<cmd1> && <cmd2> && ...'"
            #    Only applies when every segment starts with docker exec {target}
            segments = [s.strip() for s in re.split(r'\s*&&\s*', docker_cmd)]
            exec_prefix = re.compile(r'^docker\s+exec\s+\{?target\}?\s+')
            if len(segments) > 1 and all(exec_prefix.match(s) for s in segments):
                inner_parts = [exec_prefix.sub('', s) for s in segments]
                docker_cmd = "docker exec {target} bash -c '" + " && ".join(inner_parts) + "'"

            variants["docker"] = docker_cmd

        # ── Apply same {{container_name}} → {target} fix to all other variants ─
        for key, val in variants.items():
            if val and key != "docker":
                variants[key] = re.sub(r'\{\{container_name\}\}', '{target}', val)

        # ── Float uptime: awk '{print $1}' /proc/uptime → cut -d. -f1 /proc/uptime ─
        FLOAT_UPTIME = re.compile(r'''awk\s+['"]\{print\s+\$1\}['"]\s+/proc/uptime''')
        for key, val in variants.items():
            if val:
                variants[key] = FLOAT_UPTIME.sub('cut -d. -f1 /proc/uptime', val)

        # ── General: wrap bare unlabeled &&-segments as echo "label=$(cmd)" ──
        # Commands whose output is inherently formatted — leave them alone.
        _PASSTHROUGH = re.compile(
            r'\b(echo|printf|awk|sed|cut|tr|grep|wc|free|ps|df|ss|netstat|'
            r'cat|tail|head|ls|find|curl|wget|ping|nc|iostat|vmstat|'
            r'docker|kubectl|systemctl|service|journalctl|python3?|ruby|node)\b'
        )

        def _is_labeled(seg: str) -> bool:
            """True if the segment already produces key=value output."""
            seg = seg.strip()
            if not seg:
                return True
            # echo/printf with an = somewhere after them
            if re.search(r'\b(echo|printf)\b.*=', seg):
                return True
            # key=$(cmd) assignment form
            if re.search(r'\w+=\$\(', seg):
                return True
            # passthrough — complex command whose output we don't touch
            if _PASSTHROUGH.search(seg):
                return True
            return False

        def _wrap_segment(seg: str) -> str:
            seg = seg.strip()
            if not seg or _is_labeled(seg):
                return seg
            # Derive label from the command name (strip path, hyphens → underscores)
            label = seg.split()[0].split('/')[-1].replace('-', '_')
            return f'echo "{label}=$({seg})"'

        _AWK_SINGLE_Q = re.compile(r"awk\s+'([^']*)'")

        def _fix_chain(chain: str) -> str:
            parts = re.split(r'\s*&&\s*', chain)
            joined = ' && '.join(_wrap_segment(p) for p in parts)
            # awk 'pattern' inside bash -c '...' terminates the outer single-quote
            # string early. Convert to awk "pattern" with $N → \$N so awk sees
            # the right field references while the outer quoting stays valid.
            def _fix_awk(m: re.Match) -> str:
                inner = m.group(1).replace('$', r'\$')
                return f'awk "{inner}"'
            return _AWK_SINGLE_Q.sub(_fix_awk, joined)

        # Lint: nested same-type quotes inside a bash -c wrapper are a common
        # LLM failure — the inner quote closes the outer string prematurely.
        # This is the SYMMETRIC counterpart to _fix_chain's awk-single-inside-single
        # rewrite: here we handle awk-double-inside-double, which reaches the
        # user as e.g. `bash -c "ps | awk "\$3>0 {print \$1}""` — visibly broken
        # but tricky to spot in a JSON blob.
        _AWK_DOUBLE_Q = re.compile(r'awk\s+"([^"]*)"')

        def _fix_nested_double_awk(inner: str) -> str:
            """Convert `awk "..."` to `awk '...'` inside a double-quoted bash -c.
            The \\$ escapes stay put — they were escaped for the OUTER double-quote
            layer, and single quotes preserve them so awk still sees $N field refs.
            """
            return _AWK_DOUBLE_Q.sub(lambda m: f"awk '{m.group(1)}'", inner)

        # SQL clients embedded inside a single-quoted bash -c '...' are the
        # other landmine: SQL string literals need their own single quotes
        # (state='active'), but those bare `'` characters silently close the
        # outer bash string. Result is a bash-valid command whose SQL has NO
        # string literals — the DB then rejects `state=active` as an unknown
        # column. The shell-syntax validator can't catch this because bash
        # parses cleanly. Sanitize by rewriting bare `'x'` inside the SQL
        # client's -c/-e argument to the '\'' idiom, which preserves both
        # the outer bash string AND the SQL literal semantics.
        _SQL_CLIENT_ARG = re.compile(
            r'\b(psql|mysql|mysqladmin|mariadb|mongo|mongosh|redis-cli|sqlite3|'
            r'cqlsh|clickhouse-client|influx|bq)\b'
            r'([^"\']*?)'
            r'\s+-[ceE]\s+"([^"]*)"'
        )

        def _fix_sql_literals_in_single_quoted_bash(inner: str) -> str:
            """When outer bash -c uses single quotes, any bare `'` in an inner
            SQL query argument would have closed the outer string. Detect the
            common `<sqlclient> ... -c "…'x'…"` pattern and replace each bare
            single quote in the -c argument with the '\\'' idiom so the SQL
            literals survive bash's outer quoting. Idempotent — `'\\''` sequences
            are matched as-is and rewritten to themselves."""
            def repl(m: re.Match) -> str:
                client, mid, arg = m.group(1), m.group(2), m.group(3)
                if "'" not in arg:
                    return m.group(0)
                # First collapse any pre-existing '\'' back to a bare ' to keep
                # this idempotent, then re-escape every ' as '\''.
                arg = arg.replace(r"'\''", "'").replace("'", r"'\''")
                return f'{client}{mid} -c "{arg}"'
            return _SQL_CLIENT_ARG.sub(repl, inner)

        def _normalise_awk_dollar_escapes(inner: str) -> str:
            """Inside `bash -c '…'` (single-quoted outer), any `awk "…"` block
            MUST escape every `$N` as `\\$N` so bash's double-quote expansion
            leaves the field reference intact for awk. LLMs commonly produce
            mixed escaping (some `$` escaped, some not) — normalise by first
            un-escaping every `\\$` back to `$`, then re-escaping ALL `$` to
            `\\$`. Idempotent."""
            def repl(m: re.Match) -> str:
                prog = m.group(1)
                prog = prog.replace(r'\$', '$').replace('$', r'\$')
                return f'awk "{prog}"'
            return _AWK_DOUBLE_Q.sub(repl, inner)

        for key, val in list(variants.items()):
            if not val:
                continue
            # Match ANY bash -c wrapper (docker exec ... bash -c, kubectl exec ... bash -c,
            # or a bare bash -c) — not just docker, since kubernetes variants have the
            # same nested-quote failure mode.
            m = re.search(r"""(\bbash\s+-c\s+)(['"])(.+)(\2)\s*$""", val, re.DOTALL)
            if m:
                before = val[:m.start()]
                bash_prefix = m.group(1)
                outer_q = m.group(2)
                inner = m.group(3)
                if outer_q == "'":
                    # Existing behaviour: awk '...' inside bash -c '...' → awk "..." with \$
                    inner = _fix_chain(inner)
                    # Additional pass: normalise $ escaping inside any awk "..."
                    # blocks the LLM produced directly (mixed \$ / $ won't survive
                    # bash's double-quote expansion). Idempotent — safe to run after
                    # _fix_chain has already produced normalised awk "..." blocks.
                    inner = _normalise_awk_dollar_escapes(inner)
                    # Rescue SQL string literals whose bare `'` would silently
                    # close the outer single-quoted bash -c wrapper — see
                    # _fix_sql_literals_in_single_quoted_bash docstring.
                    inner = _fix_sql_literals_in_single_quoted_bash(inner)
                else:
                    # New: awk "..." inside bash -c "..." → awk '...'
                    inner = _fix_nested_double_awk(inner)
                variants[key] = f"{before}{bash_prefix}{outer_q}{inner}{outer_q}"
            else:
                variants[key] = _fix_chain(val)

        td["command_variants"] = variants

        # ── Remove container_name from parameters (it's auto-injected via {target}) ─
        params: list = td.get("parameters") or []
        td["parameters"] = [
            p for p in params
            if p.get("name") not in ("container_name",)
        ]

        # ── Safety net: always register as disabled ───────────────────────────
        td["enabled"] = False

        return td

    try:
        # ── Call 0: research correct command + realistic sample output ────────
        research_sample: list[str] = []
        output_predictable: bool = True
        call1_prompt = user_prompt
        try:
            research = await _research(provider, body.description)
            researched_cmd = research.get("command", "")
            research_sample = research.get("sample_output") or []
            output_predictable = research.get("output_predictable", True)
            if researched_cmd:
                if research_sample:
                    sample_preview = "\n".join(research_sample[:8])
                    call1_prompt = (
                        user_prompt
                        + f"\n\nResearch context — use this to determine the correct command:\n"
                        f"Correct bare command: {researched_cmd}\n"
                        f"Sample output ({len(research_sample)} lines):\n{sample_preview}\n\n"
                        "Base command_variants on the command above; add adapter wrappers as required."
                    )
                else:
                    # Research determined output is not predictable — suppress output_fields
                    call1_prompt = (
                        user_prompt
                        + f"\n\nResearch context — use this to determine the correct command:\n"
                        f"Correct bare command: {researched_cmd}\n"
                        "Output format: NOT PREDICTABLE — the command output depends on runtime "
                        "input and cannot be known in advance. Set output_fields to [].\n\n"
                        "Base command_variants on the command above; add adapter wrappers as required."
                    )
        except Exception:
            pass  # graceful fallback: proceed with 2-call flow

        # ── Call 1: tool structure ────────────────────────────────────────────
        # 4000 tokens: a full tool definition with samples and multi-adapter
        # command_variants regularly runs 2500-3500 tokens; 2000 truncated the
        # JSON mid-object and returned a "non-JSON" error. Ceiling in LLM
        # settings will clamp this down if the configured model can't do 4000.
        result_text = await provider.generate_agent_completion(
            system_prompt=system_prompt,
            user_content=call1_prompt,
            max_tokens=4000,
            temperature=0.2,
        )
        tool_def = _sanitize_tool_def(json.loads(_strip_md_fences(result_text)))

        # ── Call 2: regex patterns ────────────────────────────────────────────
        # Skip pattern generation if output is unpredictable (e.g. user-supplied script)
        if not output_predictable:
            tool_def["output_fields"] = []
        output_fields = tool_def.get("output_fields") or []
        if output_fields:
            variants = tool_def.get("command_variants") or {}
            representative_cmd = (
                variants.get("any") or variants.get("ssh") or
                variants.get("aws_ssm") or variants.get("azure") or
                next((v for v in variants.values() if v), None) or ""
            )
            # Strip docker/kubectl wrapper — use the bare inner command for context
            # Handles both {target} (sanitized form) and {{container_name}} (legacy)
            bare_cmd = re.sub(
                r'^(?:docker exec \{[^}]+\}(?:\s+bash\s+-c\s+["\'])?|kubectl exec -n \{[^}]+\} \{[^}]+\} --)\s*',
                '', representative_cmd
            ).strip()
            # Strip trailing quote if bash -c '...' wrapper was removed
            bare_cmd = bare_cmd.rstrip("'\"").strip()

            try:
                pattern_data = await _generate_patterns(
                    provider, bare_cmd, output_fields,
                    known_sample_lines=research_sample if research_sample else None,
                )
                pattern_map = {
                    f["field"]: f for f in pattern_data.get("output_fields", [])
                }
                for field in output_fields:
                    match = pattern_map.get(field["field"], {})
                    field["kind"]    = match.get("kind", "regex")
                    field["pattern"] = match.get("pattern", "")
                    field["type"]    = match.get("type", field.get("type", "string"))
            except Exception:
                # Pattern call failed — return structure without patterns rather than failing entirely
                for field in output_fields:
                    field.setdefault("kind", "regex")
                    field.setdefault("pattern", "")

        # Attach research sample so the frontend can pre-fill the "Refine" textarea.
        # Prefixed with underscore — the register endpoint will ignore unknown fields.
        if research_sample:
            tool_def["_research_sample"] = "\n".join(research_sample)

        # ── Post-generation validation ────────────────────────────────────────
        # Run every command_variant through the shared shell-syntax validator so
        # the frontend can flag broken variants (fragmented bash -c args, unclosed
        # quotes, etc.) BEFORE the user hits "Register". Non-blocking — we still
        # return the tool_def so the user can fix it inline; frontend gets a
        # parallel _validation map keyed by adapter with {ok, stage, message}.
        try:
            from agentic_os.services.command_validator import validate_action
            _val = validate_action(tool_def)
            tool_def["_validation"] = {k: v.as_dict() for k, v in _val.items()}
        except Exception as _val_err:
            # Never fail the endpoint on validator internal errors — surface them
            # as a per-key "error" so the operator sees something's off but still
            # gets the generated tool.
            tool_def["_validation"] = {"__validator_error__": str(_val_err)}

        return tool_def

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail=f"LLM returned non-JSON (truncated at {len(result_text)} chars). Tail: …{result_text[-200:]}",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/parse-output")
async def parse_tool_output_schema(body: ParseOutputRequest):
    """
    Given sample stdout from a tool command, use the LLM to infer the output_fields
    schema (field names, types, descriptions) for the catalog entry.
    """
    from agentic_os.services.summary_service import get_summary_service

    provider = get_summary_service().provider
    if not provider.is_configured():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="LLM not configured — go to Settings → LLM.",
        )

    system_prompt = (
        "You are an expert at parsing shell command output. Extract a structured schema "
        "from sample stdout, including a regex pattern with one capture group for each field. "
        "Respond with ONLY valid JSON — no markdown fences, no extra text."
    )

    cmd_context = f"\nCommand: {body.command}" if body.command else ""

    user_prompt = f"""Tool name: {body.tool_name}{cmd_context}

Sample stdout:
{body.sample_output[:3000]}

Return a JSON object:
{{
  "output_fields": [
    {{
      "field": "snake_case_field_name",
      "description": "what this value represents (under 15 words)",
      "kind": "regex",
      "pattern": "capturing regex that extracts this field from one output line",
      "type": "string | number | boolean"
    }}
  ],
  "parsing_notes": "brief note on parsing strategy (e.g. 'parse column 2 of each line', 'JSON output')"
}}

Rules:
- Only include fields that are reliably extractable from this sample (not guesses)
- Use snake_case for field names
- pattern MUST be a valid Python regex with exactly one capture group that extracts the value; write it to match a single representative line from the sample
- For JSON output set kind to "json_path" and pattern to the dot-notation path (e.g. ".items[0].name")
- type: use "number" if the captured value is always numeric, otherwise "string"
- If the output is already JSON, note it in parsing_notes"""

    try:
        result_text = await provider.generate_agent_completion(
            system_prompt=system_prompt,
            user_content=user_prompt,
            max_tokens=4000,
            temperature=0.1,
        )
        return json.loads(_strip_md_fences(result_text))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail=f"LLM returned non-JSON. Raw output: {result_text[:300]}",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/validate-process")
def validate_process(body: ProcessValidateRequest, db: Session = Depends(get_session)):
    """
    Check whether a process name is permitted for a given action.
    Returns { allowed: bool, matched_rule: {...} | null, reason: str }
    """
    repo   = ApprovedActionRepository(db)
    action = repo.get_by_tool_name(body.tool_name)

    if not action:
        return {"allowed": False, "matched_rule": None, "reason": f"Action '{body.tool_name}' not found or disabled"}

    if not action.process_rules:
        return {"allowed": True,  "matched_rule": None, "reason": "No process rules configured — allow by default"}

    rules = sorted(action.process_rules, key=lambda r: r.get("priority", 99))
    for rule in rules:
        try:
            if re.match(rule["pattern"], body.process_name):
                return {
                    "allowed":      rule["allow"],
                    "matched_rule": rule,
                    "reason": (
                        f"{'Allowed' if rule['allow'] else 'Denied'} by rule "
                        f"(priority {rule.get('priority', '?')}): {rule.get('description', rule['pattern'])}"
                    ),
                }
        except re.error:
            continue  # skip malformed regex

    return {
        "allowed":      False,
        "matched_rule": None,
        "reason": f"Process '{body.process_name}' did not match any allow rule — denied by default (whitelist policy)",
    }
