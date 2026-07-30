# Precondition reconciliation (logout action + reconcile rule)

**Date:** 2026-07-07
**Status:** SHIPPED 2026-07-07 (157 tests green; live acceptance run-928e200d + regression run-3f9beb80 both pass)

**Amendments discovered during live acceptance (both implemented):**
1. The translator never received the step's EXPECTED RESULT text — only the
   action text — so the RECONCILE rule had nothing to fire on ("the login page
   will appear" lives in `expected`). `orchestrator._execute_step` now injects
   the current step's expected result into the translator context (live path
   only, not dry-run). This supersedes "Orchestrator: unchanged" below.
2. The RECONCILE prompt rule was hardened after gpt-5.4-mini bypassed it:
   logout must be the round's ONLY action; never navigate to /login while
   authenticated (the session may bounce back to the dashboard); `login` may
   be emitted only when signing in is the step's own goal.

## Problem

The orchestrator logs in before running any steps of a case (guaranteeing that
partial step runs start authenticated). A step that *expects the login page*
then fails: the app redirects `/login` to the dashboard when a session cookie
exists, and the evaluator correctly reports "expected the login page, but the
dashboard page is visible instead."

Observed live on cycle `daYoCqgmH49VMx` (SOUSCLOUD-TR-491), TC-2 step 1
(agent run, 35.2s, FAIL).

More generally: the agent should check what state the CURRENT step assumes
(logged in / logged out / a particular page) against what the live page shows,
and fix the mismatch before executing the step.

## Decision (approved)

Keep the orchestrator pre-login exactly as is. Add the missing primitive —
a harness-executed `logout` action — and a general RECONCILE rule to the
translator prompt so the model corrects auth-state mismatches itself.

Rejected alternatives:
- **Skip pre-login when running from step 1** — assumes step 1 always handles
  login; breaks cases whose step 1 expects an authenticated dashboard.
- **Model clicks the UI Logout menu** — Logout sits in a collapsible sidebar
  and the cookie banner can block the click; exactly the flakiness class the
  `login` action was added to avoid.
- **Orchestrator keyword detection on step text** — brittle English matching;
  violates the standing requirement that the agent stays general with no
  per-case logic.

## Changes

### 1. `agent/browser.py` — new `logout` action

Add `"logout": BrowserSession._logout` to the action table. `_logout()`:

1. `context.clear_cookies()` — deterministic session kill, works from any page.
2. `goto(APP_BASE_URL, wait_until="commit")` (same commit-not-DOMContentLoaded
   rationale as `login.py` — legacy pages stall DOMContentLoaded).
3. Wait for `input[placeholder="Email address"]` to be visible (the login-page
   readiness signal used by `login.py`).
4. Dismiss the cookie-consent banner if present (non-fatal).

Raises `BrowserError` if the login page never appears. Like `login`, the
action takes no `ref` and no `value`; the model never sees credentials or
session mechanics.

### 2. `prompts/step_translator.txt` — RECONCILE rule + action list

- Add `logout` to the allowed action list (no ref, no value).
- Add rule (placed with the login rule):

  > RECONCILE FIRST: before planning actions, compare what the CURRENT step
  > requires with what the page shows. If the step expects the login page or a
  > logged-out state and PAGE ELEMENTS show you are logged in (welcome banner,
  > app sidebar), emit {"action": "logout"} first. If the step assumes you are
  > logged in and you see the login page, emit {"action": "login"} first.
  > Then continue with the step.

No orchestrator change: the act→observe loop already re-snapshots after any
URL change, so the round after a logout plans against the real login page.

### 3. Tests

- `test_browser.py`: `_logout` clears cookies, navigates, waits for the email
  field; raises `BrowserError` when the field never appears; cookie-banner
  dismissal is non-fatal.
- `test_azure_ai.py` (translator parsing): `{"action": "logout"}` round-trips
  like `login` (no ref/value required).
- `test_orchestrator.py`: a translated `logout` action is executed via
  `execute_action` like any other action.

## Not in scope

- Evaluator changes — none needed; it judges frames as before.
- Removing or conditioning the orchestrator pre-login.
- Non-auth preconditions (being on a particular page) — already covered by the
  act→observe loop, which navigates from the live snapshot each round.

## Cost

Cases that verify the login page spend ~10–15s logging in and back out.
All other cases are untouched.

## Acceptance

TC-2 step 1 ("Navigate to the Sous Chef Cloud → the login page will appear")
passes in an agent run from the Manual tab: the agent sees the dashboard,
emits `logout`, lands on the login page, and the evaluator passes the step.
Steps 2+ still pass (the model emits `login` for the login step as before).
Full pytest suite stays green.
