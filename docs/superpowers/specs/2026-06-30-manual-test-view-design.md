# Manual + Agent Test View — Design Spec

**Date:** 2026-06-30
**Status:** Approved (design); pending implementation plan
**Author:** Roman Santos + Claude Code

## Problem

The QA Agent console (FRONTEND.md) is a live view over the *automated* agent run —
the execution tape. There is no way for a tester to open a QMetry test cycle, read
the cases by hand, run them manually in the app, and record Pass/Fail/Blocked
themselves. Testers also want, per case, the option to hand the work to the AI agent
instead of doing it by hand.

This spec adds a **Manual + Agent test view**: a second console view where a tester
can, per test case, either mark the result by hand (with a note and flagged failing
steps) or run the AI agent — and then push the manual results back to QMetry behind
an explicit gate.

## Goals

- See every test case in a cycle with its steps and expected results, in a clean,
  on-brand layout.
- Mark each case **Pass / Fail / Blocked** with an optional free-text note.
- On Fail/Blocked, flag the specific step(s) that broke; that detail rides into the
  QMetry comment.
- Per case, optionally **Run with agent** and see the agent's result inline.
- **Push manual results to QMetry** behind an explicit human-in-the-loop gate.
- Hold results server-side (durable across restarts); the frontend stores no run
  data.

## Non-goals

- Changing the existing live execution-tape console (it stays as-is, on its own tab).
- Per-step manual marking (we mark per-case; steps are flagged only as failure
  context).
- Changing the `run_state` contract.
- Editing test cases or steps (read-only from the cycle).

## Constraints

- `QMETRY_API_KEY` is currently the `REPLACE_WITH…` placeholder. The page therefore
  shows fixture data and the QMetry push is disabled until the rotated key is in
  `.env`. No rework is needed when the key lands — `server.py` already auto-selects
  `QMetryCaseSource` vs `FixtureCaseSource` on the key.
- Frontend talks only to `server.py`; no credentials in the browser (CLAUDE.md).
- All UI follows FRONTEND.md tokens (Duke navy, DM Mono / Inter split, desaturated
  status colors), mobile stack at 640px, visible focus, `prefers-reduced-motion`.

## Architecture

### Navigation
`App.jsx` gains a top-level tab toggle in the stage head: **Manual** | **Live run**.
Both views share the existing navy **Rail** (brand, plan meta, case list) and
`tokens.css`. The Live view is the current execution-tape console, unchanged.
**Default tab = Manual.** The Rail's status dots reflect manual marks in Manual view
and agent results in Live view.

### Manual view (the stage)
- **Rail** case rows show manual status dots: `unmarked` (dashed), `pass` (green ✓),
  `fail` (red ✕), `blocked` (amber). Clicking a case opens it in the stage.
- **Stat strip** (reused): Total · Passed · Failed · Blocked · Remaining.
- **Case panel** (`ManualCase.jsx`):
  - Header: ID pill (mono) + case name (Inter 16/600).
  - **Read-only step list** — each row: seq # (mono), action (Inter), expected
    result (muted, `▸` prefix).
  - **Marking bar**: Pass / Fail / Blocked buttons (`*-soft` backgrounds; selected
    fills with the status color).
  - On **Fail**/**Blocked**: reveal (a) per-step **flag toggles** (flagged rows get a
    red/amber left border) and (b) a **notes** textarea.
  - **Save mark** persists to the server.
  - **Run with agent** fires a single-case agent run; a compact inline tape (reusing
    `Step.jsx`) renders the agent's result inside the panel so the tester can compare
    their call to the agent's.
- **Stage foot**: gated **Push results to QMetry** button (mirrors the Log-to-Jira
  gate). Enabled only when ≥1 case is marked, QMetry is configured, and no agent run
  is mid-flight. Disabled reason shown inline (e.g. "Connect QMetry to push
  results").

## Data contract — manual session state

A **new** server-held object (does **not** change `run_state`). Served by
`GET /manual/{plan}`:

```json
{
  "plan": {"key": "SOUSCLOUD-TP-45", "name": "Inventory · smoke test"},
  "qmetry_configured": false,
  "cases": [
    {
      "id": "IRHS-R-01",
      "name": "Create inventory recipe",
      "steps": [{"action": "...", "expected": "..."}],
      "manual": {
        "status": "unmarked",        // unmarked | pass | fail | blocked
        "comment": "",
        "failed_steps": [],           // step indices flagged on fail/blocked
        "agent_status": null,         // null | running | pass | fail | blocked
        "agent_run_id": null,
        "pushed_to_qmetry": false
      }
    }
  ],
  "summary": {"total": 5, "passed": 0, "failed": 0, "blocked": 0, "unmarked": 5, "pushed": 0}
}
```

The QMetry execution id needed to write results back stays **server-side only** and
is never serialized to the browser. This shape is documented in FRONTEND.md next to
the `run_state` contract, and a `fixtures/sample_manual_state.json` mirrors it.

## Backend endpoints (new, in `server.py`)

- `GET /manual/{plan}` — load cases via the existing `CaseSource` (fixture now,
  QMetry on key), merge stored marks, report `qmetry_configured`.
- `POST /manual/{plan}/cases/{id}/mark` body `{status, comment, failed_steps}` —
  upsert a mark; returns the updated case.
- `POST /manual/{plan}/cases/{id}/run-agent` — start a background single-case run via
  `orchestrator.run_single_case`; return a `run_id` the inline tape subscribes to
  (reusing `GET /runs/{id}` / `/stream`); store the agent result back on the case
  when done.
- `POST /manual/{plan}/push-qmetry` — **gated**. For each marked case that has an
  execution id, call the existing `QMetryClient.post_execution_result`, composing the
  comment as the tester's note plus `Failed at: step N — <action>` lines for each
  flagged step. Returns `{pushed, skipped, errors}`. Returns **409** if QMetry is not
  configured or nothing is marked. Partial per-case failures are reported, not fatal.

### Manual session store
In-memory dict keyed by plan, snapshotted to a JSON file (`manual_sessions/<plan>.json`)
so a server restart does not lose a tester's marks. The frontend holds no run data.

## Data flow

- New frontend hook `useManualState(plan)`: fetch on mount; re-fetch after each
  mark/push (manual marking is user-driven, not streamed).
- The inline agent tape reuses `useRunState(runId)` against the run id returned by
  `run-agent`.

## Error handling

- QMetry not configured → push button disabled + 409 backstop on the endpoint.
- Case with no steps → still hand-markable; an agent run BLOCKs it (as today).
- Agent run crash → inline tape shows blocked; the tester's manual mark is untouched.
- Push partial failures → reported per case; the batch still completes.
- Marking Fail/Blocked with no flagged step or note is allowed (both optional).

## Frontend file changes

```
frontend/src/
├── App.jsx                       ← add Manual | Live run tab toggle (default Manual)
├── components/
│   ├── ManualView.jsx            ← NEW — manual-mode stage
│   ├── ManualCase.jsx            ← NEW — case panel: steps, marking bar, notes, inline tape
│   └── (reuse Rail, StatStrip, Step, StageFoot)
├── hooks/
│   └── useManualState.js         ← NEW — fetch/refresh manual session
```

## Testing

- `tests/test_server.py`: mark upsert, get-merge, push gating (not-configured /
  nothing-marked / partial errors), QMetry comment composition, run-agent wiring —
  against mocked `CaseSource` + `QMetryClient`.
- `fixtures/sample_manual_state.json` + a shape-parity assertion (mirroring
  `tests/test_run_state.py`).

## Documentation

- Add a "Manual session state" section to FRONTEND.md next to the run_state contract.
- Note the new view + endpoints in CLAUDE.md's module/endpoint listings.

## Rollout

1. Build against the fixture `CaseSource`; everything works except the QMetry push,
   which shows its disabled "connect QMetry" state.
2. When the rotated `QMETRY_API_KEY` lands in `.env`, the page shows the live cycle
   (`1ZwYH2ObF7AGZa`) and the push becomes enabled — no code change.
