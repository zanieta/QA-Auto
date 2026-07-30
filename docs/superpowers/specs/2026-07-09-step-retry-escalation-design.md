# Step retries with escalating exploration + human-intervention escalation

**Date:** 2026-07-09
**Status:** SHIPPED 2026-07-09 (222 tests; live: TC-2 step 5 ran 3 attempts and
carried "NEEDS HUMAN REVIEW (3 agent attempts)"). Shipped alongside: snapshot
naming upgrades in browser.py (title attr, icon-class names like "pencil",
table-row anchoring "pencil — DSDC QA Main Account", checkbox names from
labels with live state "Asset Management (checked)"), the Account-Maintenance
pencil app-note in step_translator.txt, and step-mark buttons on the agent-run
tape entries. Ground truth via direct probe: the agent's click on the checkbox
ref toggles it correctly; an early attempt genuinely saved Asset Management
OFF (evaluator misread the frames) — restored to ON and verified. Honest
residual: even with the setting off, the sidebar menus stayed visible in the
frames — app behavior worth Roman's review; the escalation label exists for
exactly this.

**Directed by Roman (chat):** "after reading the test process, and 1
execution, on 2nd execution, it should check all the buttons instructed in the
place it was instructed … do this in every test case, maximize 3 runs and
maximum time frame for each execution unless its ongoing. then elevate to
human intervention (noted in the end)".

## Behavior

For EVERY agent-executed step, in every test case:

1. **Up to 3 attempts.** If an attempt's evaluation is not `pass`, the step
   retries (fresh act→observe loop) up to `STEP_MAX_ATTEMPTS` (default 3, env
   var, `Orchestrator(step_attempts=…)` override for tests).
2. **Escalating exploration.** Attempts after the first prepend to the
   translator context:

   ```
   ATTEMPT {n} of {max} — the previous attempt was judged {status}:
   {reason + findings}. Do not stop at the page the step names: interact
   with the specific CONTROLS it names in the PLACE it names them (row
   action icons like the pencil, buttons inside panels, checkboxes in edit
   forms) before concluding.
   ```

3. **Time budget per attempt, unless ongoing.** `STEP_ATTEMPT_BUDGET_S`
   (default 150) checked between act→observe rounds: when exceeded AND the
   previous round executed nothing, the attempt stops and goes to
   evaluation. While actions keep executing ("ongoing"), the budget does not
   interrupt; the existing round/action caps (6/20) still bound each attempt.
4. **Escalation.** When the final attempt still isn't `pass`, the step keeps
   its final status and its evaluation gains the suffix:
   `— NEEDS HUMAN REVIEW ({max} agent attempts)`. This flows into the mark's
   agent_note automatically (compose_agent_note includes evaluations), which
   is Roman's "noted in the end".

## Mechanics (agent/orchestrator.py)

- `_execute_step` wraps its live act→observe+evaluate body in an attempt
  loop. run_state is resolved ONCE (after pass or final attempt);
  intermediate attempts update `rs_step.detail` live
  (`attempt 2/3: click; click…`). Frames/executed_actions/error state reset
  per attempt. The translate-failure blocked path becomes an attempt outcome
  (retryable) rather than an immediate return — except on attempt max, where
  it resolves blocked as today.
- Guidance/performed/step_text plumbing per attempt unchanged (guidance
  looked up once, reused).
- Dry-run path: single attempt, unchanged.
- `run_plan`/cases loop unchanged; continue-past-failures still applies to
  the FINAL per-step outcome.

## Trade-off (accepted)

Genuinely failing steps now cost up to ~3× time/Azure spend. Roman explicitly
chose 3 attempts; the budget + ongoing rule keeps each attempt bounded.

## Tests (tests/test_orchestrator.py)

- fail, fail, pass evaluations → step resolves `pass`; translate context on
  attempts 2-3 contains "ATTEMPT 2 of 3"/"ATTEMPT 3 of 3" and the prior
  reason; resolve_step called once per step.
- always-fail → exactly 3 attempts, final evaluation ends with
  "NEEDS HUMAN REVIEW (3 agent attempts)", step status fail, case continues.
- pass on attempt 1 → one attempt (no behavior change).
- `step_attempts=1` restores the old single-shot behavior (used to keep the
  existing continue-past/guidance tests valid — update those tests to
  construct the orchestrator with step_attempts=1 where retries would
  distort their call-count assertions, OR adjust their expectations; state
  which in the report).

## Acceptance (live)

TC-2 step 5 (Asset Management): with the new pencil app-note + row-anchored
snapshot names + retries, the agent should reach Account → pencil (edit) →
uncheck Asset Management → Save within 3 attempts; if it still can't, the
evaluation must carry the NEEDS HUMAN REVIEW suffix and the agent note must
show it.
