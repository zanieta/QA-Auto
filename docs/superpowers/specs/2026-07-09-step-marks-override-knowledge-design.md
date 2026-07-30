# Per-step marks + override-as-knowledge

**Date:** 2026-07-09
**Status:** SHIPPED 2026-07-09 (218 tests + build green; live acceptance: 422
override-without-note; override → knowledge JSONL entry; fresh agent run of
the overridden step carried TESTER GUIDANCE into the evaluator and the verdict
followed the tester's ruling, blocked→pass, findings staying factual; UI:
per-step mark groups + derived-status pill, old case bar/flags removed.
Scope choices — auto-derived case status, skip noted in comment, knowledge
scoped to the exact same step — were the recommended defaults after Roman's
question timed out.)

## Summary

The case-level Pass/Fail/Blocked bar is replaced by per-step marks
(pass/fail/blocked/skip). The AI chip stays per step. A mark that contradicts
the AI verdict requires a note; every such override is appended to a knowledge
file and injected into the evaluator's context on future runs of that same
step.

## 1. Model — `agent/manual_state.py`

- `ManualMark.step_marks: dict[str, dict]` — keys are ORIGINAL step indices as
  strings (JSON-safe), values:
  `{"status": "pass|fail|blocked|skip", "note": str, "agent_status": str|None,
  "overrode": bool}`. Serialized in `to_dict()` (both secret and browser
  variants); `from_dict` defaults `{}`.
- `derive_case_status(step_marks) -> str`: any fail → `"fail"`; else any
  blocked → `"blocked"`; else any pass → `"pass"`; else (no marks, or only
  skips) → `"unmarked"`.
- `ManualStore.set_step_mark(plan_key, case_id, step_index, status, note,
  agent_status) -> ManualCase`:
  - validates `status in {"pass","fail","blocked","skip"}` (ValueError else);
  - `overrode = bool(agent_status) and status != agent_status`;
  - writes the entry into `step_marks[str(step_index)]`;
  - recomputes `mark.status = derive_case_status(...)` and
    `mark.failed_steps = sorted(int(i) for i, sm in step_marks.items() if
    sm["status"] == "fail")` (kept for payload/back-compat);
  - persists; returns the case.
- `set_mark` (case-level) REMAINS for the comment field only — the UI stops
  sending case status; existing endpoint untouched for compatibility.
- `compose_comment(case)` rewritten: case comment first, then ONE line per
  marked step in index order —
  `Step {n}: {status}` + ` — {note}` when note non-empty + 
  ` (overrode agent: {agent_status})` when overrode — then the agent-note
  block, blank-line separated as today. The old `Fail at: step …` lines are
  replaced by these.

## 2. Knowledge — new `agent/knowledge.py`

- `KNOWLEDGE_PATH = <repo>/knowledge/eval_overrides.jsonl` (dir auto-created).
- `record_override(plan, case_id, step_index, step_text, expected,
  agent_status, human_status, note, when) -> None` — appends one JSON line.
- `lookup_guidance(case_id, step_index, step_text) -> str` — reads the file
  (small; fine to scan), filters entries matching case_id + step_index AND
  whitespace-normalized `step_text` equality (a step edited in QMetry drops
  stale guidance), returns the newest 3 formatted as:

  ```
  - tester overrode the AI's '<agent_status>' to '<human_status>': <note>
  ```

  joined by newlines; `""` when none. Never raises — corrupt lines are
  skipped, missing file → "".

## 3. Evaluator injection — `agent/azure_ai.py` + prompt

- `evaluate_result(..., guidance: str = "")` — new optional keyword. When
  non-empty the user message gains:

  ```
  TESTER GUIDANCE — on past runs of THIS step the tester overrode the AI
  verdict (the tester is the authority on intent):
  <guidance>
  ```

- `prompts/result_evaluator.txt`:
  - input list: describe the optional TESTER GUIDANCE block;
  - output JSON gains a mandatory slot (attention lesson from 2026-07-08):
    `"guidance": "<how TESTER GUIDANCE affected this verdict, or 'none
    provided'>"` — placed FIRST, before "waived";
  - rule (top, after CONDITIONAL TRIAGE): tester guidance states the product
    owner's ruling for this exact step — when it applies to what the frames
    show, follow it over generic strictness rules; findings stay factual.
- `_parse_evaluation` ignores unknown keys already — no parser change.

## 4. Orchestrator — thread guidance + original index

- `_execute_case` passes `orig_index` into `_execute_step` (new parameter;
  tape_index stays what resolve_step uses).
- `_execute_step`, live path, before evaluation: 
  `guidance = lookup_guidance(case_id, orig_index, action_text)` wrapped in
  try/except (guidance failures must never affect a run) and passed to
  `evaluate_result(..., guidance=guidance)`.

## 5. Server — `server.py`

- `POST /manual/{plan}/cases/{case_id}/steps/{step_index}/mark`, body
  `StepMarkBody {status: str, note: str = "", agent_status: str | None =
  None}` (the frontend sends the AI chip's verdict for that step; local
  single-tester tool — client-supplied is acceptable):
  - 404 unknown case / step_index out of range;
  - 422 when status invalid, or when it contradicts a non-null agent_status
    and `note` is blank ("override requires a note");
  - calls `MANUAL.set_step_mark(...)`;
  - when overriding: `knowledge.record_override(...)` with step text/expected
    from the session case;
  - returns the updated case dict.
- Existing `/mark` endpoint stays (used now only for the case comment).

## 6. Frontend — `ManualCase.jsx` + contract

- REMOVE: the case-level STATUSES bar and the "problem here" flag checkboxes
  (`showFlags`/`failedSteps` UI). The Notes textarea stays (always visible
  now) and still saves through `/mark` with the DERIVED status unchanged —
  the mark endpoint keeps whatever status the server derived (frontend sends
  the current `m.status` back).
- Per step, next to the agent chip: four small mark buttons Pass / Fail /
  Blocked / Skip. Clicked status == agent chip status (or no chip) → saves
  immediately. Contradicts the chip → an inline note field opens under the
  step ("Why override the AI assessment?") with Save; empty note cannot save.
- Saved step marks render as an active colored button + the note text under
  the step; the case's derived status shows as a read-only pill in the case
  header.
- Summary strip unchanged (server derives).
- `useManualState.js`: `markStep(plan, caseId, stepIndex, {status, note,
  agent_status})`.
- FRONTEND.md: manual payload gains `"step_marks": {}` on the mark object;
  document the new endpoint + note-required rule + derived case status;
  update both `sample_manual_state.json` fixtures (add `"step_marks": {}`).

## Non-goals

- No QMetry step-level result API (case-level result + rich comment as
  today).
- No fuzzy/global knowledge application (same exact step only).
- run_state contract unchanged.

## Tests

- manual_state: step-mark round-trip; derivation precedence incl. all-skip →
  unmarked; failed_steps derived; compose_comment per-step lines with note /
  override / skip.
- knowledge: record + lookup round-trip; text-mismatch excluded; newest-3
  cap; missing file → "".
- azure_ai: guidance block present/absent in payload.
- orchestrator: evaluator receives guidance when lookup returns text (patch
  lookup_guidance); guidance lookup exception does not fail the step.
- server: endpoint happy path; 422 override-without-note; 404s; knowledge
  file gains a line on override; derived case status in response.
- Fixture parity restored in the frontend task.

## Acceptance (live)

1. Mark TC-2 step 5 `pass` against an agent `blocked` chip → note required;
   save with a note → knowledge/eval_overrides.jsonl gains the entry; case
   pill shows the derived status.
2. Re-run step 5 with the agent → the evaluator's request contains the
   TESTER GUIDANCE block (verify via the prompt_eval capture wrapper or log).
3. Skip a step + fail a step → case derives fail; compose_comment (unit
   level) shows both lines. No real QMetry push required.
