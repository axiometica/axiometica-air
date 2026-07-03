"""
Lightweight, synchronous event-type helpers.

These functions are designed for use in hot paths (runbook matching, policy
evaluation) where we do NOT want to call an LLM.  They only perform the
two cheapest steps of the full normalizer cascade:
  1. Canonical pass-through  — already-hierarchical codes returned as-is
  2. Alias lookup             — flat legacy names mapped to canonical codes

For full normalisation (heuristics + LLM fallback) use the async
`normalize_event_type` in event_type_normalizer.py.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def normalize_event_type(raw: str) -> str:
    """
    Fast synchronous normalisation: canonical pass-through + alias lookup.

    Returns the canonical hierarchical code when known; otherwise returns
    *raw* unchanged so callers can still compare if both sides are flat.

    Examples:
        normalize_event_type("high_cpu")
        → "infrastructure.compute.cpu_high"

        normalize_event_type("infrastructure.compute.cpu_high")
        → "infrastructure.compute.cpu_high"   (already canonical)

        normalize_event_type("totally_unknown_type")
        → "totally_unknown_type"               (pass-through)
    """
    if not raw:
        return raw

    try:
        from agentic_os.db.event_type_taxonomy_data import ALIAS_MAP, CANONICAL_CODES  # type: ignore
        if raw in CANONICAL_CODES:
            return raw                      # step 1: canonical pass-through
        canonical = ALIAS_MAP.get(raw)
        if canonical:
            logger.debug("event_type_utils: alias %r → %r", raw, canonical)
            return canonical                # step 2: alias lookup
        return raw                          # unknown — return unchanged
    except ImportError:
        return raw                          # taxonomy data not available


def event_type_matches(pattern: str, event_type: str) -> bool:
    """
    Check whether a runbook's *pattern* matches an (already-normalised)
    *event_type*.

    Supports two forms:
    • Exact match            — "infrastructure.compute.cpu_high"
    • Domain wildcard        — "infrastructure.*"  matches any code whose
                               first segment is "infrastructure"
    • Sub-domain wildcard    — "infrastructure.compute.*"  matches any code
                               under that sub-domain

    Args:
        pattern:    The event_type stored on the runbook (may include '.*').
        event_type: The canonical event type from the incident (no wildcards).

    Returns:
        True if the pattern matches the event_type.

    Examples:
        event_type_matches("infrastructure.*", "infrastructure.compute.cpu_high")  → True
        event_type_matches("infrastructure.*", "application.availability.service_down")  → False
        event_type_matches("infrastructure.compute.cpu_high", "infrastructure.compute.cpu_high")  → True
        event_type_matches("infrastructure.compute.cpu_high", "infrastructure.compute.memory_high")  → False
    """
    if not pattern or not event_type:
        return False

    if pattern.endswith(".*"):
        prefix = pattern[:-2]          # strip the trailing '.*'
        # The event_type must equal the prefix itself OR start with prefix + '.'
        return event_type == prefix or event_type.startswith(prefix + ".")

    return pattern == event_type


def find_matching_runbooks(runbooks: list, event_type: str) -> list:
    """
    Given a list of RunbookModel objects (or any objects with an `event_type`
    attribute), return those whose event_type pattern matches `event_type`.

    Normalises `event_type` first so flat legacy aliases still resolve.

    Args:
        runbooks:   Iterable of objects with an `event_type` attribute.
        event_type: Raw or canonical event type string from the incident.

    Returns:
        Filtered list in the same order as the input.
    """
    normalized = normalize_event_type(event_type)
    return [rb for rb in runbooks if event_type_matches(rb.event_type, normalized)]
