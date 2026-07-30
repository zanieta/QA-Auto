# Step-selection agent runs (Manual tab) — design

Date: 2026-07-02 · Status: approved by Roman (pending spec review)

## Goal

A tester works one test case at a time in the Manual tab. Each step of the case
has a checkbox meaning "the agent executes this step". The tester checks any
subset (e.g. steps 1–2 of 5), presses **Run selected steps with agent**, watches
the agent execute exactly those steps (with per-step screenshots and an
informational AI pass/fail hint), does the remaining steps by hand, and then
marks the case Pass / Fail / Blocked **manually**. The manual mark is the only
verdict that exists for the case; the agent never auto-marks.

## Decisions (agreed)

1. **Checkbox default: all steps checked.** Untick what you'll do by hand.
2. **Any subset is allowed.** A hint under the button warns: "The agent starts
   from the dashboard after login — do unchecked earlier steps by hand first."
   Non-contiguous selections are permitted, not forbidden.
3. **AI verdict is a hint only.** Each executed step still gets the evaluator's
   pass/fail chip + reason (and the case-level `agent_status`), but these are
   informational. The case verdict is the tester's manual mark, as today.
4. **Fresh browser session per agent run** (unchanged). Anything the tester did
   in their own browser is not visible to the agent. A shared/persistent
   session is explicitly out of scope for this iteration.
5. **Run-state contract unchanged.** A partial run's tape simply contains only
   the executed steps. No new step status value is introduced; unchecked steps
   are visible in the Manual checklist, not in the run tape.

## Backend changes

### `agent/orchestrator.py`
- `run_single_case(case_id, plan_key="", dry_run=False, step_indices=None)`.
  When `step_indices` (a sequence of 0-based ints) is given, `_execute_case`
  executes only those steps of the case, in ascending index order. Indices out
  of range are ignored. `None` (default) = all steps — existing behavior.

### `server.py`
- `POST /manual/{plan}/cases/{case_id}/run-agent` body becomes optional JSON
  `{"steps": [0, 1]}`. Missing body or missing key = all steps (backward
  compatible). Empty list → 422 ("select at least one step").
- The selected indices are recorded on the manual case (`agent_steps`) so the
  frontend can show which steps the last agent run covered.

### `agent/manual_state.py`
- `ManualCase` gains `agent_steps: list[int] | None = None`, serialized in the
  session JSON. Overwritten on every run-agent call: the selected indices, or
  `None` when the run covered all steps. Snapshot round-trips it.

## Frontend changes (Manual tab, per FRONTEND.md language)

In the case detail view:
- Each step row gains a checkbox (checked by default). Mono index + action text
  as today; expected result beneath.
- Button **"Run selected steps with agent"** (primary navy) — disabled while an
  agent run is in progress or when zero boxes are checked. Below it, the muted
  hint sentence from decision 2.
- While running: the tape for the run (existing `GET /runs/{id}` subscription)
  shows only the selected steps resolving, with screenshots.
- After the run: each executed step shows the agent chip (`agent: pass` green /
  `agent: fail` red, with the evaluator reason on hover/expand). Steps not
  executed show no chip.
- The existing Pass / Fail / Blocked mark buttons are unchanged and remain the
  only way to set the case verdict. Push-to-QMetry gating unchanged.

## FRONTEND.md updates (same change)

- Manual session state: document `agent_steps` on the case's `manual` object.
- Endpoints: document the optional `{"steps": [...]}` body on run-agent.
- Marking UX: add the checkbox flow + copy ("Run selected steps with agent",
  the dashboard hint sentence).

## Error handling

- Empty selection → 422 from the server; button disabled client-side anyway.
- Out-of-range indices are ignored server-side (defensive; UI can't produce them).
- A failing selected step still stops the case run at that step (existing
  stop-on-fail-within-case behavior), and `agent_status` records `fail`.
- One case crashing never kills the server (existing behavior, unchanged).

## Tests

- Orchestrator: `step_indices` runs only those steps (mock browser/azure);
  `None` runs all; out-of-range ignored; order ascending.
- Server: run-agent with `{"steps":[0,1]}` passes indices through; empty list
  422s; missing body runs all (regression).
- Manual state: `agent_steps` serializes + snapshots.
- Frontend contract: fixture `sample_manual_state.json` gains `agent_steps`.

## Out of scope (explicit)

- Shared/persistent browser session between tester and agent.
- Step selection on Live-tab whole-plan runs.
- Any change to the AI evaluator's role beyond labeling it a hint.
