"""Override-as-knowledge store.

When a tester's per-step mark contradicts the AI evaluator's verdict (see
`agent/manual_state.py::set_step_mark`), the override is appended to a durable
knowledge file — JSON Lines, one override per line. `lookup_guidance`
re-reads that file on later runs of the *exact same* step (same case_id +
step_index + step text) and formats the most recent overrides into a short
guidance block that gets injected into the evaluator's prompt context (see
`agent/azure_ai.py::evaluate_result`).

There is no fuzzy or global matching by design — a step edited in QMetry no
longer matches its old text and its stale guidance is silently dropped.

`lookup_guidance` never raises: a missing file returns "", and corrupt lines
are skipped rather than aborting the read.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "eval_overrides.jsonl"


def _normalize(text: str) -> str:
    """Whitespace-normalize step text so trivial formatting differences don't
    break the match, while a genuine edit (different wording) still misses."""
    return " ".join((text or "").split())


def record_override(
    plan: str,
    case_id: str,
    step_index: int,
    step_text: str,
    expected: str,
    agent_status: str | None,
    human_status: str,
    note: str,
    when: str,
) -> None:
    """Append one override entry to the knowledge file.

    Creates the parent directory (`knowledge/`) if it doesn't exist yet.
    """
    entry: dict[str, Any] = {
        "plan": plan,
        "case_id": case_id,
        "step_index": step_index,
        "step_text": step_text,
        "expected": expected,
        "agent_status": agent_status,
        "human_status": human_status,
        "note": note,
        "when": when,
    }
    KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with KNOWLEDGE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def lookup_guidance(case_id: str, step_index: int, step_text: str) -> str:
    """Return the newest overrides recorded for this exact step, formatted
    for injection into the evaluator prompt; "" when none are found.

    Matching requires case_id + step_index equality AND whitespace-normalized
    step_text equality — a step whose text changed in QMetry no longer
    matches its stale guidance. Never raises.
    """
    try:
        return _lookup_guidance(case_id, step_index, step_text)
    except Exception:
        log.warning("lookup_guidance failed for %s/%s", case_id, step_index, exc_info=True)
        return ""


def _lookup_guidance(case_id: str, step_index: int, step_text: str) -> str:
    if not KNOWLEDGE_PATH.exists():
        return ""

    normalized_text = _normalize(step_text)
    matches: list[dict[str, Any]] = []
    for line in KNOWLEDGE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            log.warning("skipping corrupt knowledge line: %r", line)
            continue
        if not isinstance(entry, dict):
            log.warning("skipping non-object knowledge line: %r", line)
            continue
        if entry.get("case_id") != case_id:
            continue
        if entry.get("step_index") != step_index:
            continue
        if _normalize(entry.get("step_text", "")) != normalized_text:
            continue
        matches.append(entry)

    newest = matches[-3:]
    if not newest:
        return ""

    return "\n".join(
        f"- tester overrode the AI's '{m.get('agent_status')}' to "
        f"'{m.get('human_status')}': {m.get('note')}"
        for m in newest
    )
