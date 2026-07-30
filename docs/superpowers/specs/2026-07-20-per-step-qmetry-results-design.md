# Per-step QMetry results, create-or-edit execution

**Date:** 2026-07-20
**Status:** DESIGN — not yet implemented.

**Directed by Roman (chat):** "when a test case is ran and passed, in QMetry
you should execute and click all pass for each test step … there are 2
conditions: 1 create a new test execution, 2 edit the existing" — plus, on
follow-up, "run first then decide if execute or just test" (→ the QMetry write
must be an explicit, gated commit, never an automatic side-effect of a run).

## Problem

Today the ONLY thing that writes to QMetry is the Manual-tab push
(`server.py` → `push_manual_to_qmetry` → `QMetryClient.post_execution_result`),
and it writes a **single case-level** result — one `executionResultId` on the
test-case execution. The individual test-step rows inside that execution stay
blank/unexecuted, so QMetry shows the case as Passed with an empty step grid.
CLI / live-console agent runs (`main.py`, `orchestrator.py`) never write to
QMetry at all — they read cases, run them, and produce an HTML report.

We want: after a case runs, write **every step's real status** (Pass / Fail /
Blocked) into QMetry so the step grid mirrors what the agent (or tester)
actually saw — and let the operator choose whether that lands in the existing
execution or a fresh one.

## Decisions (settled with Roman)

1. **Both modes, chosen at the end of the run (config flag = default).**
   Support writing into the existing execution (edit-in-place) AND creating a
   new execution. **AMENDED 2026-07-21 (Roman):** the choice is made by the
   tester *at the end of the run*, not only by config — after the agent finishes
   the steps, the console asks "reflect results on the **current** execution, or
   **create a new** execution?" and pushes per that answer. `QMETRY_EXECUTION_MODE`
   (default `edit`) is only the fallback when no explicit choice is passed. The
   push endpoints accept an optional `mode` ("edit"|"create") in the request body.
2. **"Create" = a new execution RUN inside the same cycle**, not a new test
   cycle. QMetry supports re-execution natively; cloning cycles per run would
   clutter the project and break the cycle-key convention the whole app uses.
   (If the same-cycle re-run endpoint turns out not to exist, STOP and confirm
   with Roman before falling back to new-cycle.)
3. **Each step gets its real status** — passed steps → Pass, the failing/blocked
   step → Fail/Blocked, later steps → whatever status they got. Not "all pass or
   nothing."
4. **Applies to both flows** — Manual-tab push and agent runs (CLI + live
   console).
5. **Gated commit, never automatic.** A plain `main.py --plan` or console Run
   still only reads + runs + reports. Results reach QMetry only when the operator
   explicitly pushes (Manual button, console push button, or `--push-qmetry`).

## Architecture

**One shared writer, two callers.** All QMetry write logic lives in one new
helper in `agent/qmetry.py`. Both the Manual push and the agent-run push call
it, so there is no duplicated write path to drift.

### New `QMetryClient` methods

The real step-execution and create-execution endpoint shapes are NOT wired yet
and — per this project's history (`agent/qmetry.py` docstrings: shapes differ
from the published spec, reverse-engineered from live calls) — must be captured
from the live API before coding against them. **Implementation task 1 is a
read-only probe** (`scripts/qmetry_probe.py`) against a throwaway execution to
capture the exact JSON shapes and verify step ordering. Only the create path
performs a real write, and that is shown to Roman before it runs against
anything real.

- `get_test_step_executions(cycle_id, exec_id)` → ordered step-execution rows,
  each carrying its own id + a result slot. Order MUST match our flattened
  step order (see risk below).
- `post_step_execution_result(cycle_id, exec_id, step_exec_id, status, comment)`
  → set one step's Pass/Fail/Blocked (+ optional comment). Reuses the existing
  `_exec_result_cache` (status name → result id) built by
  `get_execution_results`.
- `create_execution(cycle_id, tc_id, version_no)` → create a fresh execution run
  of the case in the cycle, return its new execution id. Create-mode only.

### The writer helper — `write_case_execution(...)`

Signature (in `agent/qmetry.py`, takes a `QMetryClient`):

```
write_case_execution(
    client, *, cycle_id, execution_id, tc_id, version_no,
    case_status, step_results, mode, comment=None,
) -> WriteResult
```

- `step_results`: mapping of **flattened step index → (status, comment|None)** —
  only the steps we actually have a status for (filtered/partial runs omit the
  rest).
- Flow:
  1. `mode == "create"` → `create_execution(...)` → use the new exec id.
     `mode == "edit"` → use the passed `execution_id`.
  2. `get_test_step_executions(...)` → ordered rows.
  3. Map `step_results` onto rows **by position**; `post_step_execution_result`
     for each. A per-step failure is collected, NOT fatal.
  4. `post_execution_result(...)` for the case-level status (existing method).
  5. Return `WriteResult(exec_id, steps_written, errors)`.

### Step-mapping risk (the one real hazard)

Our step lists are already **flattened** — `_load_steps` expands shareable
steps inline. QMetry's step-execution rows should be in that same flat order, so
position-mapping works. **The probe MUST verify this alignment explicitly** on a
case that contains a shareable step; a mismatch would mark the wrong steps. If
they don't align by position, fall back to matching on step id / seqNo and note
it — do not ship position-mapping unverified.

### Config + gating

- `QMETRY_EXECUTION_MODE = edit | create` (env, default `edit`), overridable per
  request/CLI arg.
- Write surfaces (all gated, all read/run first):
  - **Manual tab** — existing push button + `POST /manual/{plan}/push-qmetry`,
    now writing per-step via the helper. Data source: `mark.step_marks`
    (per-step status + notes already present).
  - **Live console** — new `POST /runs/{id}/push-qmetry` + a gated button
    mirroring the Manual one (disabled during a run). Data source: the run's
    `run_state` case + steps.
  - **CLI** — new `main.py --push-qmetry` flag (off by default). Data source:
    the finished `run_state`.

### Per-step data sources

- **Manual push:** `ManualMark.step_marks` — `{index: {status, note, ...}}`.
  Already the per-step truth the tester entered; notes become step comments.
- **Agent runs:** `run_state` case steps — each `Step` carries
  `status` (pass/fail/blocked) and `evaluation` (the AI reason), which becomes
  the step comment. Mapping run_state tape index → original/flattened index must
  account for filtered runs (`step_indices`), same as `compose_agent_note`
  already does.

## Error handling

- Per-step post failure → collected into `WriteResult.errors`, run continues;
  the case-level result is still posted.
- Unknown status name → skipped (existing `post_execution_result` behavior).
- `create_execution` failure → abort that case's write, report the error; do not
  fall through to editing a random execution.
- Nothing here can crash a run — writing is a post-run action.

## Tests

- `tests/test_qmetry.py`: the three new methods (mocked httpx); the writer
  helper's create-vs-edit branching; position-mapping of `step_results` onto
  step rows; per-step-error collection is non-fatal.
- `tests/test_server.py`: `POST /runs/{id}/push-qmetry` and the upgraded
  `POST /manual/{plan}/push-qmetry` both call the writer with per-step data;
  gating (no push mid-run, no push with nothing to write).

## Out of scope

- New test *cycle* creation (explicitly rejected in decision 2).
- Automatic write-back on a plain run (rejected — writes are gated).
- Screenshot/attachment upload to QMetry step executions (future; run_state
  now carries `screenshot_b64` per step, so this is possible later).
