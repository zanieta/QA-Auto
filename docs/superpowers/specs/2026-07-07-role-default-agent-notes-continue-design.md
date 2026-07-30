# Role defaulting + agent-run notes + continue-past-failures

**Date:** 2026-07-07
**Status:** SHIPPED 2026-07-07 evening (165 tests green; live acceptance
run-1af92d33 all-steps-execute + note format, run-73a9ef11 role unblock; final
review READY — one optional hardening noted: range-filter step_indices in
compose_agent_note to match the orchestrator's filter)

Three small features that make agent runs match how Roman actually tests.

## 1. Role defaulting (prompt-only)

**Problem:** test cases name roles — "Recipe Admin", "Admin", often as broken
`[~id]` references rendered as "[unresolved reference]". The evaluator blocks
steps because frames can't prove the signed-in user has the named role
(observed: TC-2 step 2 "role permissions not explicitly verified"). The
harness always signs in as the one configured test account, which Roman says
to treat as the required role unless a step explicitly says otherwise.

**Changes (no code):**
- `prompts/result_evaluator.txt` — add rule: unless the step explicitly
  requires signing in with a different named account/credentials, treat the
  signed-in account as HAVING whatever role the case references ("Admin",
  "Recipe Admin", or an "[unresolved reference]" role token). Never fail or
  block a step solely because the frames cannot prove the account's
  role/identity matches the named role.
- `prompts/step_translator.txt` — matching rule: a role mention is not an
  instruction to find different credentials; the harness `login` action's
  account IS the referenced role unless the step itself provides other
  credentials (which the model must still never type — `login` only).

## 2. Agent findings → case note

**Decision (Roman):** after each agent run, the AI's per-step verdicts +
findings are automatically recorded on the case, timestamped; the tester's own
typed note stays untouched; the note goes to QMetry as part of the comment on
push. Latest run wins (the full history stays in run_state / reports).

**Changes:**
- `agent/manual_state.py`:
  - `ManualMark.agent_note: str = ""` — new field, in `to_dict`/`from_dict`
    (serialized to the frontend like the rest of the mark; persists in
    `manual_sessions/<plan>.json`).
  - `ManualStore.set_agent(...)` gains keyword `agent_note` with an `_UNSET`
    sentinel like `agent_steps` (run start leaves it; run completion sets it).
  - `compose_comment(case)` appends the agent note (if non-empty) after the
    tester comment and flagged-step lines, separated by a blank line.
- `server.py` `_run_agent_case`: on completion, build the note from the
  finished RunState case and save via `set_agent`:

  ```
  Agent run 2026-07-07 14:32 (run-928e200d), steps 1, 3-5: fail
  Step 1: pass — The login page is visible as expected.
  Step 3: pass — All expected sidebar menus are visible.
  Step 4: fail — Expected navigation … not demonstrated.
  Step 5: pass — …
  ```

  Step numbers are the ORIGINAL case step numbers (selected indices + 1), not
  tape positions. The evaluation text is the step's `evaluation` (reason +
  findings) as stored in run_state. On a crashed run the note records
  `Agent run <ts> (<run_id>): crashed — see server log`.
- Frontend: show `manual.agent_note` in the case card below the note field —
  DM Mono, small, muted; hidden when empty. Update FRONTEND.md's "Manual
  session state" shape in the same change (contract rule).

## 3. Continue past failed/blocked steps (agent runs)

**Problem:** `orchestrator._execute_case` breaks the per-case loop on the
first fail/blocked step, so later selected steps never run (observed: step 4
fail → step 5 skipped). Roman: run every selected step to the end; a failed
step's dependents will fail/block on their own evidence.

**Decision (Roman):** continue past BOTH fail and blocked. No English-based
dependency analysis: each step already starts from a live page snapshot with
RECONCILE (login/logout/navigate as needed), and the evaluator's `blocked`
verdict already reports "precondition not demonstrated" — that IS the
dependency handling.

**Changes:**
- `agent/orchestrator.py` `_execute_case`: remove the `break` on fail and on
  blocked; run all selected steps. Case outcome precedence: `fail` if any
  step failed, else `blocked` if any step blocked, else `pass`.
- The case brief's `done: fail` / `done: blocked` markers (already built per
  step) tell the translator what happened earlier — no prompt change needed.
- Still stops a case entirely: browser/login failure at case open (existing
  behavior), and per-case crash handling (unchanged).
- `RUN_MODE` (continue | stop_on_fail across CASES) is untouched.

## Not in scope

- run_state shape changes (none — steps already carry status/evaluation).
- Agent-run history UI (latest-run-wins; history lives in RUNS + reports).
- Jira auto-bug logic (unchanged; fires on case outcome fail as before).

## Tests

- `test_manual_state.py` (or wherever ManualMark is tested): `agent_note`
  round-trips to_dict/from_dict (default "" for old snapshots);
  `set_agent` sentinel leaves the note when omitted, sets it when given;
  `compose_comment` includes comment + flagged steps + agent note in order.
- `test_server.py`: after a manual agent run completes, the mark's
  `agent_note` contains the header (run id, step numbers, outcome) and one
  line per executed step with its status and evaluation text.
- `test_orchestrator.py`: (a) step 1 of 3 fails → all 3 execute, case
  outcome fail; (b) no fails but one blocked → all execute, case blocked;
  (c) all pass → pass (existing).
- Prompts are data — no prompt-text tests.

## Acceptance (live)

On cycle daYoCqgmH49VMx TC-2, agent steps 1,3,4,5 (skip 2): step 4's fail no
longer stops step 5; the case card shows an agent note with one line per run
step; pushing to QMetry includes the note in the comment. TC-2 step 2 (when
run) no longer blocks on the unresolved role reference.
