# Per-case target URL (server) override — design

**Date:** 2026-08-18
**Status:** SUPERSEDED 2026-08-19 — implemented, then replaced. Kept as the
record of the decision and why it changed.

> **What changed and why.** This was built as specified (commit c0115a3), and
> using it showed the scope was wrong: the tester wants ONE server for the whole
> console, not a value per test case. It is now a single global setting shown in
> the left rail and persisted in `settings.json` (commits dae056a, 5b320e9),
> with the per-case mechanism removed rather than left beside it — two controls
> for one decision is how a run ends up pointed somewhere nobody intended.
>
> Also dropped: the `APP_ENVIRONMENTS` Test/Production dropdown described below.
> A plain URL box replaced it, which removed more code than it added — the
> derived-selection logic and the `newLinkIntent` flag existed only to keep a
> dropdown in sync with the text box.
>
> What SURVIVED this reversal, and is still live: the base-URL semantics (login
> and later relative navigations resolve against the same base), `login()`
> reading `browser.base_url` instead of the env var directly, save-time URL
> validation, the non-test-server warning, and the decision to leave
> `run_state.json` untouched. See FRONTEND.md and CLAUDE.md for current
> behaviour; this file is history.

## Problem

Test cases name the server they run against in their own step text. TC-2442
step 1 literally says: open a browser, type the URL in the address bar —
Test Server `https://test.souscheftech.com/login` or Production Server
`https://souscheftech.com/login`, press Enter.

The agent ignores all of that: every run goes to whatever `APP_BASE_URL` says.
A tester cannot run one case against Test and the next against Production
without editing `.env` and restarting the server.

## Solution

A per-case server slot in the Manual tab, sitting beside the existing per-case
login credentials and plumbed through the same path. It offers the known
servers as a dropdown plus a free-text address bar for anything else. Empty
falls back to `APP_BASE_URL`.

The value is a **base URL override**: login goes there, and relative
navigations in later steps resolve against it. It is not a "deep link to visit
after login".

## Scope

Per case only. Not a run-level override, not a global console switch. (The
credentials plumbing has both a run level and a case level; the URL needs only
the case level, and adding the run level later is the same one-argument pattern.)

## 1. Where the value lives

`ManualMark` in `agent/manual_state.py` grows:

```python
target_url: str = ""   # per-case server; "" = use APP_BASE_URL
```

Persisted in `manual_sessions/<plan>.json` via the existing `_persist`, and
serialized in `to_dict()` / read in `from_dict()`. Unlike `login_password` it is
NOT a secret: it ships in every `/manual` payload and may be logged.

`ManualStore.set_target_url(plan_key, case_id, url) -> ManualCase` mirrors
`set_credentials`: an empty string clears back to the `.env` default.

## 2. Where "Test" and "Production" come from

No hardcoded URLs (CLAUDE.md). New env var, name=url pairs, comma separated:

```
APP_ENVIRONMENTS=Test=https://test.souscheftech.com/login,Production=https://souscheftech.com/login
```

`GET /config` grows two non-secret keys:

```json
{
  "default_cycle": "...",
  "environments": [{"name": "Test", "url": "https://test.souscheftech.com/login"}],
  "default_url": "https://test.souscheftech.com/login"
}
```

`default_url` is `APP_BASE_URL`. Malformed `APP_ENVIRONMENTS` entries are
skipped with a warning rather than crashing the endpoint; unset yields `[]`,
and the UI still offers the free-text address bar.

Adding a third server later is an `.env` edit, not a code change.

**Warning rule:** the UI shows a warning whenever the effective URL differs
from `default_url`. There is no separate "is this production?" flag to keep in
sync — anything that is not the normal test target gets flagged.

## 3. API

```
POST /manual/{plan}/cases/{case_id}/target-url   {"url": "https://…"}
```

Returns the updated `case.to_dict()`, exactly like the credentials endpoint.

Kept off `/mark` deliberately: `/mark` carries verdict semantics (it derives
case status from step marks) and a server choice is not a verdict.

Validation: `""` clears the override. Otherwise the URL must parse with scheme
`http` or `https` and a non-empty host, else **422** with a readable message.
Validating at save time means a typo fails immediately instead of surfacing as
a mysterious BLOCKED login 40 seconds into a run.

## 4. Threading it into the run

The credentials path, mirrored:

- `server._manual_case_target_urls(plan) -> {case_id: url}` (skips empties),
  alongside the existing `_manual_case_credentials`.
- `Orchestrator.run_plan(..., case_target_urls: dict[str, str] | None = None)`
- `Orchestrator.run_single_case(..., target_url: str | None = None)`
- both reach `_execute_case(..., target_url=...)`, which sets
  `browser.base_url = target_url.rstrip("/")` when truthy, immediately after
  `browser_factory()` and beside the existing `browser.credentials = ...` line.
- Logged once per case at INFO (not a secret).

### Supporting fix: login() must honor the override

`agent/login.py` currently reads `os.environ.get("APP_BASE_URL")` directly, so a
per-case URL would be silently ignored at the one step that matters most. It
changes to read `browser.base_url`, which:

- is the identical value when no override is set (BrowserSession already
  defaults `base_url` from `APP_BASE_URL`), so behaviour is unchanged by
  default; and
- makes login resolve against the same base that `execute_action` already uses
  for relative navigations.

The "email field never appeared" error text keeps naming the URL it actually
tried.

## 5. UI

`frontend/src/components/TargetUrlRow.jsx`, modelled on `CredentialsRow.jsx`,
rendered in `ManualCase.jsx` directly above the "Login as" row:

```
Server    [ Test ▾ ]  [ https://test.souscheftech.com/login ]  [Save]
⚠ Non-test server — the agent will click Save/Delete against live data.
```

- The `<select>` lists `environments` from `/config`, plus `Custom…`.
- Picking a named server fills the address bar; `Custom…` leaves it editable.
- On load, a stored `target_url` matching a known environment selects it;
  otherwise the select shows `Custom…` with the URL in the box.
- Empty box = "use the default", shown as help text naming `default_url`.
- Warning banner whenever the effective URL differs from `default_url`.
- DM Mono for the URL, existing warn token for the banner, existing
  `manual-credentials-*` CSS conventions for layout. No new design vocabulary.
- Saved via `useManualState`'s existing pattern (a `setTargetUrl` alongside
  `setCredentials`).

FRONTEND.md gains this row in the Manual-case component spec and the two new
`/config` keys.

## 6. run_state is NOT touched

The URL does not go into `run_state.json`. That file is the shared
backend/frontend contract, and the information is already visible two ways: the
manual mark carries it, and `agent/url_banner.py` stamps the real page URL onto
every screenshot, so the HTML report already shows which server each step ran
against. Leaving the contract alone avoids a coordinated FRONTEND.md + model +
hook change for information nobody is missing.

## 7. Tests

- `tests/test_manual_state.py` — set / clear / persist round-trip; `to_dict`
  carries `target_url`; a stored mark without the key loads as `""`.
- `tests/test_server.py` — endpoint happy path; 422 on a malformed URL; 404 on
  an unknown case; `APP_ENVIRONMENTS` parsed into `/config` (including unset
  and one malformed entry); `_manual_case_target_urls` skips empties.
- `tests/test_orchestrator.py` — override reaches `browser.base_url`; empty or
  absent leaves the factory default; a per-case value applies to that case only.
- `tests/test_login.py` — login navigates to `browser.base_url`, and an
  overridden base_url is what appears in the failure message.

## Non-goals

- Run-level or global server switching.
- A confirm dialog before production runs (warning only, by decision).
- Any change to how QMetry results are pushed.
