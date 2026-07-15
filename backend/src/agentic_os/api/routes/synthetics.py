"""
Synthetic Monitors API

GET    /api/synthetics                    → list all monitors
POST   /api/synthetics                    → create monitor
GET    /api/synthetics/{id}               → get monitor
PUT    /api/synthetics/{id}               → update monitor
DELETE /api/synthetics/{id}               → delete monitor
POST   /api/synthetics/{id}/run           → trigger immediate run
POST   /api/synthetics/generate           → LLM script generation (conversation)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session
from agentic_os.db.repositories import SyntheticMonitorRepository
from agentic_os.security.crypto import decrypt_fields, encrypt_fields

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Pydantic schemas ───────────────────────────────────────────────────────────

class SyntheticMonitorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    har_filename: Optional[str] = None
    script: Optional[str] = None
    pages: Optional[List[Dict[str, Any]]] = None   # parsed HAR pages incl. assertions; stored as JSON
    credentials: Optional[Dict[str, str]] = None   # plain dict; stored encrypted
    schedule_mins: int = Field(default=15, ge=1, le=10080)
    enabled: bool = True


class SyntheticMonitorUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    har_filename: Optional[str] = None
    script: Optional[str] = None
    pages: Optional[List[Dict[str, Any]]] = None
    credentials: Optional[Dict[str, str]] = None
    schedule_mins: Optional[int] = Field(None, ge=1, le=10080)
    enabled: Optional[bool] = None


class GenerateRequest(BaseModel):
    current_script: str   # the deterministically-generated script that failed tests
    error_output: str     # test output to diagnose and fix


class GenerateResponse(BaseModel):
    script: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _encrypt_credentials(creds: Optional[Dict[str, str]]) -> Optional[str]:
    if not creds:
        return None
    encrypted = encrypt_fields(creds, list(creds.keys()))
    return json.dumps(encrypted)


def _decrypt_credentials(enc: Optional[str]) -> Dict[str, str]:
    if not enc:
        return {}
    try:
        data = json.loads(enc)
        return decrypt_fields(data, list(data.keys()))
    except Exception:
        return {}


def _row_with_plain_credentials(row: dict) -> dict:
    """Return monitor dict with credentials_enc replaced by decrypted dict,
    and pages_json parsed back into a `pages` list."""
    result = dict(row)
    result["credentials"] = _decrypt_credentials(result.pop("credentials_enc", None))
    pages_json = result.pop("pages_json", None)
    try:
        result["pages"] = json.loads(pages_json) if pages_json else None
    except (TypeError, ValueError):
        result["pages"] = None
    return result


# ── CRUD ───────────────────────────────────────────────────────────────────────

@router.get("/synthetics", tags=["Synthetics"])
def list_synthetics(db: Session = Depends(get_session)):
    repo = SyntheticMonitorRepository(db)
    rows = repo.list_all()
    return [_row_with_plain_credentials(r) for r in rows]


@router.post("/synthetics", status_code=201, tags=["Synthetics"])
def create_synthetic(payload: SyntheticMonitorCreate, db: Session = Depends(get_session)):
    repo = SyntheticMonitorRepository(db)
    data = {
        "name":            payload.name,
        "har_filename":    payload.har_filename,
        "script":          payload.script,
        "pages_json":      json.dumps(payload.pages) if payload.pages is not None else None,
        "credentials_enc": _encrypt_credentials(payload.credentials),
        "schedule_mins":   payload.schedule_mins,
        "enabled":         payload.enabled,
    }
    row = repo.create(data)
    return _row_with_plain_credentials(row)


@router.get("/synthetics/{monitor_id}", tags=["Synthetics"])
def get_synthetic(monitor_id: str, db: Session = Depends(get_session)):
    repo = SyntheticMonitorRepository(db)
    row = repo.get(monitor_id)
    if not row:
        raise HTTPException(status_code=404, detail="Synthetic monitor not found")
    return _row_with_plain_credentials(row)


@router.put("/synthetics/{monitor_id}", tags=["Synthetics"])
def update_synthetic(
    monitor_id: str,
    payload: SyntheticMonitorUpdate,
    db: Session = Depends(get_session),
):
    repo = SyntheticMonitorRepository(db)
    existing = repo.get(monitor_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Synthetic monitor not found")

    data: Dict[str, Any] = {}
    if payload.name is not None:
        data["name"] = payload.name
    if payload.har_filename is not None:
        data["har_filename"] = payload.har_filename
    if payload.script is not None:
        data["script"] = payload.script
    if payload.pages is not None:
        data["pages_json"] = json.dumps(payload.pages)
    if payload.credentials is not None:
        data["credentials_enc"] = _encrypt_credentials(payload.credentials)
    if payload.schedule_mins is not None:
        data["schedule_mins"] = payload.schedule_mins
    if payload.enabled is not None:
        data["enabled"] = payload.enabled

    row = repo.update(monitor_id, data)
    return _row_with_plain_credentials(row)


@router.delete("/synthetics/{monitor_id}", status_code=204, tags=["Synthetics"])
def delete_synthetic(monitor_id: str, db: Session = Depends(get_session)):
    repo = SyntheticMonitorRepository(db)
    if not repo.delete(monitor_id):
        raise HTTPException(status_code=404, detail="Synthetic monitor not found")


# ── Trigger immediate run ──────────────────────────────────────────────────────

def _run_script_in_subprocess(monitor: dict) -> tuple[str, str]:
    """Run a synthetic monitor script in a temp file. Returns (status, output)."""
    script = monitor.get("script")
    if not script:
        return "error", "No script saved for this monitor."

    creds = _decrypt_credentials(monitor.get("credentials_enc"))

    env = dict(os.environ)
    env.update(creds)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(script)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout or "") + (result.stderr or "")
        status = "pass" if result.returncode == 0 else "fail"
        return status, output
    except subprocess.TimeoutExpired:
        return "error", "Script timed out after 120 seconds."
    except Exception as exc:
        return "error", f"{type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.post("/synthetics/{monitor_id}/run", tags=["Synthetics"])
def run_synthetic_now(
    monitor_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    repo = SyntheticMonitorRepository(db)
    row = repo.get(monitor_id)
    if not row:
        raise HTTPException(status_code=404, detail="Synthetic monitor not found")
    if not row.get("script"):
        raise HTTPException(status_code=400, detail="Monitor has no script — generate one first.")

    def _do_run():
        with next(get_session()) as session:
            r = SyntheticMonitorRepository(session)
            mon = r.get(monitor_id)
            if not mon:
                return
            status, output = _run_script_in_subprocess(mon)
            r.update_last_run(monitor_id, status, output)
            logger.info(f"[SYNTHETICS] Manual run of '{mon['name']}': {status}")

    background_tasks.add_task(_do_run)
    return {"status": "queued", "monitor_id": monitor_id}


class TestScriptPayload(BaseModel):
    script: str
    credentials: Optional[Dict[str, str]] = None


class TestScriptResult(BaseModel):
    status: str    # pass | fail | error
    output: str


@router.post("/synthetics/test", response_model=TestScriptResult, tags=["Synthetics"])
def test_script(payload: TestScriptPayload):
    """Run a script without saving it — used during the generate/test loop.

    Credentials are injected directly into the subprocess env — no Fernet
    round-trip so there is no silent failure if the key is misconfigured.
    """
    creds = payload.credentials or {}
    env = dict(os.environ)
    env.update({k: str(v) for k, v in creds.items()})

    script = payload.script
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(script)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout or "") + (result.stderr or "")
        status = "pass" if result.returncode == 0 else "fail"
        return TestScriptResult(status=status, output=output)
    except subprocess.TimeoutExpired:
        return TestScriptResult(status="error", output="Script timed out after 120 seconds.")
    except Exception as exc:
        return TestScriptResult(status="error", output=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


class RunResultPayload(BaseModel):
    status: str         # pass | fail | error
    output: str


@router.post("/synthetics/{monitor_id}/result", tags=["Synthetics"])
def post_run_result(
    monitor_id: str,
    payload: RunResultPayload,
    db: Session = Depends(get_session),
):
    """Called by the watcher after it runs a synthetic monitor to record the result."""
    repo = SyntheticMonitorRepository(db)
    row = repo.get(monitor_id)
    if not row:
        raise HTTPException(status_code=404, detail="Synthetic monitor not found")
    repo.update_last_run(monitor_id, payload.status, payload.output)
    return {"ok": True}


# ── LLM Script Fix ─────────────────────────────────────────────────────────────

_FIX_PROMPT = (
    "You are a Python QA engineer. The user gives you a failing synthetic monitoring script "
    "and its error output. Return ONLY the corrected Python script — no markdown, no backticks, "
    "no explanations. Preserve all pages and the exact output format."
)


def _get_llm_config(db: Session) -> Optional[dict]:
    from agentic_os.db.llm_config_repository import LLMConfigRepository
    repo = LLMConfigRepository(db)
    return repo.get_config("default")


@router.post("/synthetics/generate", response_model=GenerateResponse, tags=["Synthetics"])
async def generate_script(payload: GenerateRequest, db: Session = Depends(get_session)):
    """
    Stateless LLM fix: given a failing script and its error output, return a corrected script.
    Scripts are generated deterministically on the frontend; this endpoint is only called
    when the user clicks 'Fix with AI' after a failed test run.
    """
    llm_cfg = _get_llm_config(db)
    if not llm_cfg or not llm_cfg.get("api_key"):
        raise HTTPException(
            status_code=503,
            detail="LLM not configured — go to Settings > LLM to add your API key.",
        )

    api_key  = llm_cfg["api_key"]
    model    = llm_cfg.get("model") or "gpt-4o"
    base_url = llm_cfg.get("base_url")

    messages = [
        {"role": "system", "content": _FIX_PROMPT},
        {
            "role": "user",
            "content": (
                f"Script:\n```python\n{payload.current_script}\n```\n\n"
                f"Error output:\n```\n{payload.error_output[:3000]}\n```\n\n"
                "Return the corrected Python script only."
            ),
        },
    ]

    try:
        import httpx as _httpx

        llm_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        endpoint = (base_url or "https://api.openai.com") + "/v1/chat/completions"

        def _strip_fences(text: str) -> str:
            if text.startswith("```"):
                return "\n".join(
                    line for line in text.splitlines()
                    if not line.startswith("```")
                ).strip()
            return text

        async with _httpx.AsyncClient(timeout=120) as http:
            resp = await http.post(
                endpoint,
                headers=llm_headers,
                json={"model": model, "messages": messages, "temperature": 0.1},
            )
            resp.raise_for_status()
            script_text = _strip_fences(resp.json()["choices"][0]["message"]["content"].strip())

        return GenerateResponse(script=script_text)

    except _httpx.HTTPStatusError as exc:
        logger.error(f"[SYNTHETICS] LLM API error: {exc.response.status_code} {exc.response.text[:300]}")
        raise HTTPException(status_code=502, detail=f"LLM API returned {exc.response.status_code}")
    except Exception as exc:
        logger.error(f"[SYNTHETICS] LLM call failed: {exc}")
        raise HTTPException(status_code=502, detail=f"LLM error: {type(exc).__name__}: {exc}")
