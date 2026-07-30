# DOM-Grounded Agent Actions — Design Spec

**Date:** 2026-06-30
**Status:** Approved (design); pending implementation plan
**Author:** Roman Santos + Claude Code

## Problem

The agent's automatic run works end-to-end (login → translate → execute → evaluate),
but the **translate** step is blind: GPT-4o invents CSS selectors from the English
step text without seeing the page, so it guesses selectors that don't exist
(e.g. `[data-test='main-nav']`). Actions then time out and the step/case is marked
FAIL even when the app is fine — the pass/fail verdicts aren't trustworthy.

## Goal

Ground the translate→act loop in the **real page** so the model can only act on
elements that actually exist, and recover once from a miss. Make the agent's
verdicts trustworthy without changing the evaluation step.

## Non-goals

- Vision-based clicking / coordinate control.
- Multi-step planning or DOM-diffing across steps.
- Changing the screenshot→pass/fail evaluation (stays GPT-4o vision).
- Swapping the model/provider (separate effort).

## Constraints

- Python 3.14, async, `.venv\Scripts\python.exe`.
- Tests mock the Playwright `Page` — no real Chromium in the suite.
- Server runs single-process with the ProactorEventLoop (see
  `project_agent_run_requirements`); unaffected by this change.
- Keep the existing action vocabulary: navigate, click, fill, select, wait,
  assert_text, assert_visible.

## Approach (decided)

- **Targeting = reference-by-id.** The page is snapshotted into a list of
  interactive elements, each tagged with a stable `ref`. The model returns
  `{action, ref}` and can only choose refs that exist.
- **Recovery = re-snapshot + retry once.** On an action failure, take a fresh
  snapshot, re-translate with the failure reason, retry the step once, then FAIL.

## Architecture — per-step flow

```
snapshot_elements()  →  translate_step(text, context, elements)  →  [{action, ref, value}]
  →  execute_action (resolve ref → element)  →  on BrowserError: re-snapshot +
     re-translate(with error) + retry once  →  screenshot  →  evaluate_result (unchanged)
```

## Components

### `agent/browser.py` — `snapshot_elements()`
A new async method that runs ONE `page.evaluate(js)` which:
- selects visible interactive elements: `button`, `a[href]`, `input`, `textarea`,
  `select`, and `[role]` in {button, link, tab, menuitem, checkbox, radio};
- sets `data-agent-ref="e{n}"` on each (n increments; reset each snapshot);
- collects `{ref, tag, role, name}` where `name` = aria-label || visible text ||
  placeholder || value (trimmed, truncated to ~80 chars).

Returns `list[dict]`, capped at `MAX_SNAPSHOT_ELEMENTS = 60` (logs a warning when
truncated). The `data-agent-ref` attribute makes a ref resolvable: element actions
run against the locator `[data-agent-ref="e7"]`.

### Action schema — add `ref`
Actions are `{action, ref?, value?, selector?}`:
- Element actions (click/fill/select/wait/assert_text/assert_visible) use `ref`;
  `browser.py` resolves `ref` → `[data-agent-ref="{ref}"]` and runs the existing
  handler. `value` carries fill/select/assert text.
- `navigate` uses `value` (URL).
- `selector` remains accepted as a fallback (back-compat; not emitted by the new
  prompt).

`execute_action` resolves `ref` to a CSS selector at dispatch time; handlers are
otherwise unchanged.

### `agent/azure_ai.py` + `prompts/step_translator.txt`
`translate_step(step_text, app_context=None, elements=None)` injects the element
list into the user message, e.g.:
```
STEP: Click Logout
PAGE ELEMENTS (choose by ref; only use refs that exist):
  e3  input   "Email address"
  e7  link    "Logout"
For navigation, use {"action":"navigate","value":"<url>"}.
```
The prompt instructs the model to return actions that reference `ref` for element
actions and `value` for navigate. `_parse_actions` accepts a `ref` field (alongside
the existing wrapper-shape tolerance) and passes it through. When `elements` is
empty/None (dry-run), the prompt omits the element block and the model may still
emit a navigate or a best-effort action.

### `agent/orchestrator.py` — heal-once
`_execute_step` (non-dry-run):
1. `elements = await browser.snapshot_elements()`
2. `actions = await azure.translate_step(action_text, app_context, elements)`
3. execute each action; on the first `BrowserError`:
   - `elements2 = await browser.snapshot_elements()`
   - `actions = await azure.translate_step(action_text, ctx_with_error, elements2)`
     where the context notes "previous attempt failed: <reason>"
   - re-execute; if it fails again → resolve step FAIL with the reason.
4. screenshot + evaluate as today.

Dry-run path: skip snapshot (no browser); call `translate_step(text, "dry-run mode")`
with no elements, resolve the step as today.

## Error handling

- Snapshot failure (e.g. `page.evaluate` throws) → log, treat as empty element list;
  translation proceeds (may produce navigate-only or fail cleanly).
- A `ref` the model returns that no longer resolves → the action's locator times out
  → BrowserError → triggers the one heal-retry.
- Heal-retry exhausted → step FAIL with the underlying reason (existing behavior).
- One bad case never kills the run (existing per-case guard).

## Testing (TDD, mocked Page)

- `test_browser.py`: `snapshot_elements()` returns refs from a mocked
  `page.evaluate` return value; cap/truncation logged; an element action with a
  `ref` resolves to `[data-agent-ref="…"]` (assert the click/ fill selector).
- `test_azure_ai.py`: `translate_step` includes the element list in the request
  body; `_parse_actions` parses `{action, ref, value}`; dry-run (no elements) still
  works.
- `test_orchestrator.py`: on a first `BrowserError`, the orchestrator re-snapshots,
  re-translates, retries once, and (a) passes if the retry succeeds, (b) FAILs if it
  fails again. Assert `translate_step`/`snapshot_elements` call counts.

## Documentation

- Note the `ref` action field and `snapshot_elements` in `CLAUDE.md`'s
  `agent/browser.py` and `agent/azure_ai.py` entries.
- The action JSON shape (`{action, ref, value}`) is internal to the agent — no
  FRONTEND.md/run_state change.

## Rollout

Works against the existing live cycle immediately. Expectation: agent runs that
previously FAILed on guessed selectors now act on real elements; remaining FAILs
reflect genuine app behavior or steps whose target isn't an interactive element.
