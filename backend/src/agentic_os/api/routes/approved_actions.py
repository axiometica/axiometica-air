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
        "- docker:     ALWAYS prefix with 'docker exec {{container_name}} <cmd>' — "
        "the command runs INSIDE the named container, not on the host.\n"
        "- ssh:        bare shell command, runs on the remote host directly.\n"
        "- kubernetes: ALWAYS prefix with 'kubectl exec -n {{namespace}} {{pod_name}} -- <cmd>' "
        "to run inside a pod, OR use kubectl CLI commands (kubectl get, kubectl describe, etc.).\n"
        "- aws_ssm:    bare shell command, delivered via SSM Run Command to an EC2 instance.\n"
        "- azure:      bare shell command, delivered via Azure Run Command to a VM.\n"
        "- any:        fallback bare shell command for unrecognised adapters.\n\n"
        "Use {{param_name}} placeholders for runtime values. "
        "Respond with ONLY valid JSON — no markdown fences, no extra text."
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
  "parameters": [
    {{"name": "param_name", "type": "string|number|boolean", "required": true, "description": "what it is", "default": null}}
  ],
  "output_fields": [
    {{"field": "snake_case_field", "description": "what this value represents"}}
  ]
}}

Rules:
- blast_radius: 1=read-only, 2=safe change, 3=service impact, 4=data risk, 5=destructive
- requires_approval should be true for blast_radius >= 3
- infer ALL parameters from {{{{placeholders}}}} used in command_variants — include container_name, namespace, pod_name if those prefixes are used
- output_fields: list every useful value the command prints that a downstream runbook step might need (counts, names, statuses, IPs, PIDs) — do NOT leave this empty for diagnostic tools
- use null only for adapters where the command genuinely cannot apply"""

    try:
        result_text = await provider.generate_agent_completion(
            system_prompt=system_prompt,
            user_content=user_prompt,
            max_tokens=1500,
            temperature=0.2,
        )
        return json.loads(result_text)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail=f"LLM returned non-JSON. Raw output: {result_text[:300]}",
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
        "from sample stdout. Respond with ONLY valid JSON — no markdown fences, no extra text."
    )

    cmd_context = f"\nCommand: {body.command}" if body.command else ""

    user_prompt = f"""Tool name: {body.tool_name}{cmd_context}

Sample stdout:
{body.sample_output[:3000]}

Return a JSON object:
{{
  "output_fields": [
    {{"field": "snake_case_field_name", "description": "what this value represents"}}
  ],
  "parsing_notes": "brief note on parsing strategy (e.g. 'parse column 2 of each line', 'JSON output')"
}}

Rules:
- Only include fields that are reliably extractable from this sample (not guesses)
- Use snake_case for field names
- Keep descriptions concise (under 15 words)
- If the output is already JSON, note it in parsing_notes"""

    try:
        result_text = await provider.generate_agent_completion(
            system_prompt=system_prompt,
            user_content=user_prompt,
            max_tokens=600,
            temperature=0.1,
        )
        return json.loads(result_text)
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
