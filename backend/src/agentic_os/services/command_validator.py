"""
Shell-syntax validator for approved-action commands.

Every approved_action.command_variants[<adapter>] entry is a string that will
eventually be executed as shell input. LLM-generated tools frequently ship
with subtle quoting bugs where the command LOOKS right but bash actually
splits it into fragments before it reaches the intended interpreter — those
tools then silently fail when a runbook tries to use them.

Two consumers:
  1. Tool Builder generate endpoint — validates newly-generated variants
     before returning them (or at least surfaces the failure to the user).
  2. Admin sweep endpoint — audits every existing tool in the catalog and
     produces a report of broken variants so operators can fix them.

Validation is a two-step bash-based check:

  Step 1: parse the OUTER command with `bash -n -c '…'`. Rejects any command
          that isn't syntactically valid bash on its own.
  Step 2: for any `bash -c` / `sh -c` wrapper found by shlex-tokenising the
          command, extract the wrapper's argument and validate THAT with
          `bash -n` too. Catches the class of bug where a bash -c argument
          fragmented across multiple argv positions due to unbalanced inner
          quoting — the outer command still parses, but the extracted script
          is an unclosed fragment (e.g. `awk -v d="$(date -d 15`).

Runtime placeholders like `{target}`, `{{namespace}}` are substituted with
a safe token before validation so brace-expansion doesn't produce false
positives.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

# Runtime placeholders — single {name}, {name.field}, or double {{name}}.
# Substituted with a safe token before validation so bash doesn't try to
# interpret them as brace expansion or fail on unknown constructs.
_PLACEHOLDER_RE = re.compile(r"\{\{?[a-z_][a-z_0-9.]*\}?\}", re.IGNORECASE)
_PLACEHOLDER_TOKEN = "PH"

# `bash -c` / `sh -c` — the standard "run this string as a script" pattern.
# Any command that includes this wrapper has its -c argument validated
# independently in step 2.
_WRAPPER_INTERPRETERS = ("bash", "sh")

# PowerShell / Windows commands — bash can't parse `@{...}` hash tables,
# `[math]::Round()` static-method calls, or PowerShell cmdlet syntax. Skip
# bash-based validation for these entirely rather than reporting false-positive
# syntax errors. Matching is intentionally conservative — a single strong
# indicator is enough to flip a command into "not-bash" territory.
_POWERSHELL_INDICATORS = re.compile(
    r'\b(?:'
    r'Invoke-Command|Invoke-Expression|Invoke-RestMethod|Invoke-WebRequest|'
    r'Get-(?:CimInstance|WmiObject|Process|Service|EventLog|ChildItem|Content|'
        r'Item|Location|Date|Random|Member)|'
    r'Set-(?:CimInstance|Service|Location|Content|Item|Variable|ExecutionPolicy)|'
    r'Test-(?:Path|Connection|NetConnection)|'
    r'New-(?:Item|Object|PSSession|CimSession)|'
    r'Remove-(?:Item|Service|Variable)|'
    r'Start-(?:Service|Process|Sleep)|'
    r'Stop-(?:Service|Process|Computer)|'
    r'Restart-(?:Service|Computer)|'
    r'ForEach-Object|Where-Object|Select-Object|Sort-Object|Measure-Object|'
    r'Out-(?:Null|File|String|GridView)'
    r')\b'
    r'|\[math\]::'
    r'|@\{[^}]*=',
    re.IGNORECASE,
)


@dataclass
class ValidationResult:
    ok: bool
    stage: str          # "outer" | "shlex" | "inner_script" | "substitution"
    message: Optional[str] = None   # human-readable error; None when ok

    def as_dict(self) -> dict:
        return {"ok": self.ok, "stage": self.stage, "message": self.message}


def _run_bash_n(script: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Run `bash -n -c <script>` — parse-only, no execution.
    Returns (ok, stderr). stderr is empty on success."""
    try:
        proc = subprocess.run(
            ["bash", "-n", "-c", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode == 0, proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, f"bash -n timed out after {timeout}s"
    except FileNotFoundError:
        return False, "bash not available on this host"


def validate_command(command: str) -> ValidationResult:
    """
    Validate a single command string. See module docstring for the two-step
    check performed. Returns a ValidationResult — .ok True means the command
    parses cleanly; False means the .message describes what went wrong.
    """
    if not command or not command.strip():
        # Empty is valid — approved_actions may have command=None for
        # controller-dispatched tools (nothing to validate).
        return ValidationResult(ok=True, stage="outer")

    # PowerShell/Windows commands aren't parseable by bash; skip rather than
    # generate false-positive syntax errors. Report the skip transparently
    # so operators know why the command wasn't validated.
    if _POWERSHELL_INDICATORS.search(command):
        return ValidationResult(
            ok=True, stage="skipped-powershell",
            message="PowerShell / Windows command — bash validator does not apply",
        )

    substituted = _PLACEHOLDER_RE.sub(_PLACEHOLDER_TOKEN, command)

    # Step 1: does the outer command parse?
    outer_ok, outer_err = _run_bash_n(substituted)
    if not outer_ok:
        return ValidationResult(ok=False, stage="outer", message=outer_err)

    # Step 2: find any bash -c / sh -c and validate the -c argument.
    try:
        tokens = shlex.split(substituted, posix=True)
    except ValueError as exc:
        # shlex disagrees with bash about quoting — often means bash accepted
        # something shlex thinks is unbalanced, or vice versa. Report but
        # don't treat as fatal; outer bash -n already passed.
        return ValidationResult(ok=False, stage="shlex", message=str(exc))

    for i in range(len(tokens) - 2):
        if tokens[i] in _WRAPPER_INTERPRETERS and tokens[i + 1] == "-c":
            inner = tokens[i + 2]
            inner_ok, inner_err = _run_bash_n(inner)
            if not inner_ok:
                # The -c argument (as bash parsed it) isn't a valid script.
                # Nearly always means the intended script fragmented across
                # multiple argv positions due to broken outer quoting.
                trailing = tokens[i + 3:]
                trailing_hint = ""
                if trailing:
                    trailing_hint = (
                        f"  (bash also parsed {len(trailing)} trailing token(s) "
                        f"after the -c argument, suggesting the intended script "
                        f"fragmented — first extra: {trailing[0]!r})"
                    )
                return ValidationResult(
                    ok=False,
                    stage="inner_script",
                    message=f"{tokens[i]} -c argument doesn't parse: {inner_err}{trailing_hint}",
                )

    return ValidationResult(ok=True, stage="outer")


def validate_action(action) -> dict[str, ValidationResult]:
    """
    Validate every non-empty command / command_variant of an ApprovedAction
    (SQLAlchemy model instance or plain dict). Returns a dict keyed by
    adapter name ("command", "docker", "ssh", …) whose values are per-variant
    ValidationResults. Adapters with empty commands are skipped entirely.
    """
    def _get(field: str):
        if isinstance(action, dict):
            return action.get(field)
        return getattr(action, field, None)

    results: dict[str, ValidationResult] = {}

    top_cmd = _get("command")
    if top_cmd:
        results["command"] = validate_command(top_cmd)

    variants = _get("command_variants") or {}
    if isinstance(variants, dict):
        for adapter, cmd in variants.items():
            if cmd:
                results[adapter] = validate_command(cmd)

    return results
