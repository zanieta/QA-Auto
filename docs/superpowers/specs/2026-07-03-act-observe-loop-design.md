# Act→observe loop for step execution — design

Date: 2026-07-03 · Status: building (Roman hit the multi-page wall 3× on TC-2/TC-3 step 4)

## Problem

A step is translated ONCE against one page snapshot. Any action that navigates
invalidates every later ref in the plan ("click failed on [data-agent-ref=e11]:
Timeout"). Multi-page steps ("Click each menu to navigate to its respective
section") are impossible by construction.

## Design

Replace the translate-once + heal-retry step executor with a bounded loop:

```
rounds = 0
progress = []                      # executed actions, human-readable
while rounds < MAX_ROUNDS (6):
    snapshot current page
    actions, done = translate(step, case brief + progress so far + last error)
    if done: break                 # model says the step's goal is complete
    for each action:
        execute
        capture frame (quick settle; lost frame never fails the step)
        append to progress
        if page URL changed: break to re-observe (refs are stale now)
    on BrowserError: record as last_error; next round re-observes
    if total executed actions > MAX_ACTIONS (20): break
settle; final frame; evaluate(frames[-8:], expected)  # unchanged from today
```

Key properties:
- **Single-page steps behave as today** — round 1 executes every action, no
  URL change, next round the model returns done. One extra cheap translate.
- **Navigation is a first-class event**: after it, the agent re-looks and
  re-plans the remainder with full progress context. This subsumes the old
  heal-retry (a failed action = same re-observe path, with the error in
  context).
- **Findings-first evaluation unchanged** — frames from every round feed the
  evaluator; pass/fail/blocked + findings as built earlier today.
- **Hard caps**: 6 rounds, 20 actions, existing 15s per-action timeout. On cap
  exhaustion the step is still evaluated on the evidence gathered (the
  evaluator can say blocked).

## Translator contract change

Output gains an optional flag:
  {"actions": [...], "done": false}
- done:true (actions ignored/empty) = "the step's goal is already met; stop".
- Round 1 with no actions and no done stays an error (protects against
  degenerate output).
- `translate_step` returns `(actions, done)`; dry-run and all callers updated.

## Prompt changes (step_translator.txt)

- Explain the loop: "you will be called repeatedly; PROGRESS lists what you
  already did; plan only what is doable on the CURRENT page; after a
  navigation you will be called again."
- Multi-page steps: do the next page-visit, don't try to plan all nine.
- Remove the now-obsolete single-shot heal wording ("previous attempt failed,
  pick a different ref" stays, as the error rides in context).

## run_state contract

Unchanged. `detail` accumulates all executed actions across rounds; one final
screenshot on the step as today.

## Out of scope

- Step 5-class mutations (Asset Management) stay human/blocked — the loop
  makes them *possible* but they need a guaranteed-revert design first.
- UI changes: none.

## Acceptance

TC-3 step 4 ("Click each menu…") completes without ref-timeout errors and
resolves pass/fail/blocked with findings — no `[data-agent-ref]` TimeoutError.

## Tests

- azure: translate_step parses {"actions": [], "done": true} → ([], True);
  legacy list output → (actions, False).
- orchestrator: multi-round loop — URL change triggers re-observe with
  progress context; done stops; MAX_ROUNDS caps; frames accumulate across
  rounds; single-page steps still make exactly 2 translate calls (plan+done).
- Full suite stays green.
