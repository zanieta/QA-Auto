# Findings say what the agent did + conditional clauses pass by absence

**Date:** 2026-07-08
**Status:** SHIPPED 2026-07-08 (185 tests green; live: step 1 findings name
Chromium ✓).

**Acceptance-driven amendments:**
1. `evaluate_result` also receives `step_text` (the step's ACTION text) — the
   "If available" qualifiers live there, not in the EXPECTED text, so the
   evaluator could not see them; orchestrator passes `action_text`.
2. CONDITIONAL CLAUSES rule strengthened twice (inherits-the-condition wording;
   absence = PASS never blocked/fail; the fail rule now cross-references it).

**Amendment 3 (2026-07-08 night, after Roman's semantics ruling):** gpt-4o
ignored the mid-list conditional rule (baseline 5/5 FAIL on captured inputs).
Fixed with two prompt changes validated by an offline repeated-judgment
harness (scratchpad capture_eval_inputs.py / judge_repeated.py — the
eval_real_expected pattern): (1) CONDITIONAL TRIAGE moved to the top of the
reasoning rules as a mandatory first step; (2) the output JSON gained a
`waived` field the model must fill FIRST (absent conditional features →
"not available — waived", excluded from the verdict; parser ignores the extra
key). Result: conditional inputs 5/5 PASS, unconditional variant 3/3 FAIL
(no global leniency), live pipeline run run-c011c5d8 PASS with "ACTIVE/
INACTIVE toggle was not available, so it was waived." Roman's ruling
supersedes the earlier caveat: absence on the visited pages = waived,
regardless of whether the feature exists elsewhere in the app.

**Superseded note — earlier caveat on the worked example (TC-2 step 4):** the toggle EXISTS in
this app (Edit Inventory), so pass-by-absence only applies when the agent
actually tours all pages. When the translator under-tours (known gpt-5.4-mini
compound-step limit, documented 2026-07-03), the evaluator honestly reports
the toggle as not demonstrated — verdicts on this step vary with tour depth
(full tour run-1af92d33 PASSED it same day). The conditional rule pays off on
genuinely-absent features; the durable fix for compound tour steps remains
splitting them in QMetry.

## 1. PERFORMED ACTIONS reach the evaluator

**Problem:** the evaluator sees only frames + expected text, so findings can't
say what the agent used ("Open Chrome, Firefox, or Edge" → which one?).

**Changes:**
- `agent/azure_ai.py` `evaluate_result(screenshots, expected, performed: str = "")`
  — new optional keyword. When non-empty, the user message gains a block:

  ```
  PERFORMED ACTIONS — what the agent actually did, in order:
  <performed>
  ```

  Signature stays backward-compatible (default "").
- `agent/orchestrator.py` `_execute_step`: the evaluation call becomes
  `evaluate_result(frames[-8:], expected, performed=_format_detail(executed_actions))`
  (`executed_actions` is in scope at that point; dry-run path unchanged).
- `prompts/result_evaluator.txt`:
  - Input list: add the PERFORMED ACTIONS block description.
  - Findings rule: when the step offers alternatives or optional parts, the
    findings must state HOW the outcome was achieved — which control/path the
    agent used, drawn from PERFORMED ACTIONS.
  - Standing fact: the harness browser is ALWAYS Chromium (equivalent to
    Google Chrome). A step offering a browser choice (Chrome / Firefox /
    Edge) is satisfied by it; findings name it ("performed in Chromium,
    Chrome-equivalent"). Never fail/block over the browser choice.

## 2. Conditional clauses ("If available…") pass by absence

- `prompts/result_evaluator.txt`, new rule placed BEFORE the
  doubt-means-fail rule:

  > CONDITIONAL CLAUSES: a requirement prefixed "If available", "If
  > required", "if present", "where applicable" (or similar) is conditional.
  > When the frames and PERFORMED ACTIONS show the described feature was not
  > present, that clause is SATISFIED BY ABSENCE — findings must say so
  > ("ACTIVE/INACTIVE toggle: not available on these pages") and the verdict
  > comes from the step's unconditional parts alone. Never fail or block a
  > step solely because an "if" feature was absent. When the feature IS
  > present, judge it normally.

- `prompts/step_translator.txt`, new rule: conditional clauses are optional —
  after completing the unconditional part, if the control the clause
  describes is not in PAGE ELEMENTS, skip the clause and finish the step
  (done); do not spend rounds hunting for it.

## Tests

- `test_azure_ai.py`: `evaluate_result(..., performed="click; click")` puts
  the text in the request payload; omitted → no PERFORMED ACTIONS block.
- `test_orchestrator.py`: the fake evaluator receives `performed` containing
  the executed detail (assert via captured call kwargs/args).
- Prompts are data — no text assertions.

## Acceptance (live)

- TC-2 step 4 agent run ("Click each menu… If available, toggle…"): verdict
  pass (or fail only for a real defect), findings mention the toggle's
  availability explicitly and name what was clicked.
- TC-2 step 1 findings mention the browser used (Chromium / Chrome).
