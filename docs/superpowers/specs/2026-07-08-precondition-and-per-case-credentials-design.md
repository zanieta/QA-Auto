# Precondition display + per-case login credentials

**Date:** 2026-07-08
**Status:** SHIPPED 2026-07-08 (178 tests green; live: precondition rendered on
TC-2, no login_password in any /manual payload, wrong per-case creds reached
the real login form and were rejected → blocked, cleared → env admin passed;
final review READY)

## 1. Precondition on the case card

**Problem:** QMetry test cases carry a Precondition (see TC-2: "User has valid
Recipe Admin credentials", etc.). The Manual tab doesn't show it; testers need
it between the case title and the steps.

**API fact (verified live 2026-07-08 on cycle daYoCqgmH49VMx TC-2):**
`GET /testcases/{id}/versions/{no}` returns NO precondition by default; with
`?fields=summary,precondition` the response gains `precondition` (Jira wiki
markup: `#` numbered lists, `*bold*`). `?fields=all` does NOT work — fields
must be named explicitly.

**Changes:**
- `agent/qmetry.py`:
  - `get_test_case_version_detail(tc_id, version_no)` sends
    `params={"fields": "summary,precondition"}`.
  - `QMetryCaseSource.list_cases` `_hydrate` reads `detail.get("precondition")`
    and puts `precondition` (cleaned via the existing `clean_step_text`) on the
    case dict; `""` when absent.
- `agent/manual_state.py`: `ManualCase.precondition: str = ""`; included in
  `to_dict()` as `"precondition"`; `ManualStore.build` passes
  `rc.get("precondition", "")`.
- Frontend `ManualCase.jsx`: render a "Precondition" block between the case
  title row and the agent-hint line ("The agent starts from the dashboard…"),
  muted panel style consistent with the agent-note block; hidden when empty.
- FRONTEND.md manual-session JSON: add `"precondition": ""` on the case object
  (sibling of `steps`) AND `"login_username": ""` / `"has_password": false` on
  the manual mark object (feature 2's browser payload); update both
  `sample_manual_state.json` fixtures to match for parity.
- `FixtureCaseSource` cases without the key default to `""` — no fixture-plan
  change required for the input side.

## 2. Per-case login credentials

**Decision (Roman):** persisted and replaceable. Passwords are stored
server-side in `manual_sessions/<plan>.json` (same trust level as `.env` on
this machine) and are NEVER sent back to the browser; the UI shows that a
password is saved and typing a new one replaces it.

**Model (`agent/manual_state.py`):**
- `ManualMark.login_username: str = ""` and `login_password: str = ""`.
- `to_dict()` (the browser payload) contains `"login_username"` and
  `"has_password": bool(self.login_password)` — NOT the password itself.
- Persistence must still round-trip the password across server restarts:
  `to_dict()` is also what `_persist` writes. Resolution: `to_dict()` gains a
  keyword `include_secrets: bool = False`; `_persist` calls
  `to_dict(include_secrets=True)` which adds `"login_password"`. `from_dict`
  reads `login_password` when present. The session/browser path keeps the
  default (no secret).
- `ManualStore.set_credentials(plan_key, case_id, username, password) ->
  ManualCase`: sets both; empty username AND empty password clears both
  (back to the default .env admin). A non-empty username with empty password
  keeps the existing password (lets Roman fix a typo'd username without
  retyping the password).

**Endpoint (`server.py`):**
- `POST /manual/{plan}/cases/{case_id}/credentials` with body
  `{"username": str, "password": str}` (pydantic model; both default `""`).
  Returns the updated case dict (no password in it). 404 on unknown case.
  Deliberately separate from `/mark` so status/notes updates never carry
  credentials.

**Agent wiring:**
- `agent/browser.py`: `BrowserSession.credentials: tuple[str, str] | None`
  attribute (default None), set externally before/at login time. No behavior
  change in browser.py itself.
- `agent/login.py` `login(browser)`: if `browser.credentials` is set, use it
  for username/password instead of the env vars (env still required as
  fallback; the "credentials missing" error only fires when neither source
  provides them).
- `agent/orchestrator.py`: `run_single_case(..., credentials: tuple[str, str]
  | None = None)` (and the underlying `_execute_case`) sets
  `browser.credentials = credentials` right after `browser_factory()` /
  before `open_session()`+`login()`. Full-plan runs (`run_plan`) are
  UNCHANGED — per-case credentials are a Manual-tab feature.
- `server.py` `_run_agent_case`: reads the case's mark; if
  `login_username` and `login_password` are both non-empty, passes
  `credentials=(username, password)` to `run_single_case`.
- The model still only emits `{"action": "login"}`; the harness resolves which
  account. RECONCILE re-logins mid-case therefore also use the per-case
  account automatically (they go through the same `login(browser)`).

**UI (`ManualCase.jsx`):**
- "Login as" row near the agent controls: username input + password input +
  Save button hitting the credentials endpoint. Password input `type=password`,
  value kept locally only; when `has_password` is true and the local field is
  empty, placeholder reads "••• saved". Saving with both fields empty clears
  to the default admin (helper text says so).

**Security invariants (unchanged from project rules):**
- No credential in any HTTP response, run_state, report, QMetry comment, or
  Jira bug.
- The AI model never receives credentials — prompts and contexts untouched.

## Tests

- manual_state: password round-trips via persist/from_dict but is absent from
  browser `to_dict()`; `has_password` flag; `set_credentials` clear semantics
  (both-empty clears; username-only keeps password); `precondition` in
  `to_dict`.
- server: credentials endpoint sets/clears and never echoes the password;
  `_run_agent_case` passes credentials tuple to `run_single_case` when set and
  None when not.
- orchestrator: `run_single_case(credentials=...)` lands on
  `browser.credentials` before login.
- login: `login()` prefers `browser.credentials` over env; falls back to env;
  errors only when both missing.
- qmetry: `get_test_case_version_detail` sends the `fields` param;
  `list_cases` carries cleaned `precondition` (mock httpx as existing tests
  do).
- Fixture parity for the new `precondition` + credential-related mark keys.

## Acceptance (live)

- TC-2's card shows its precondition block ("User has valid Recipe Admin
  credentials…") between title and steps.
- Setting a per-case username/password and running agent step 2: the browser
  signs in with that account (server log shows the login; run passes/fails on
  its own merits). GET /manual response contains no password anywhere.
- With credentials cleared, runs use the .env admin as before.
