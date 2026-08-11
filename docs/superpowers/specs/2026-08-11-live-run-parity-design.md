# Live-run parity with the Manual panel — design

**Date:** 2026-08-11
**Status:** Approved, implementation deferred ("proceed in a while")
**Author:** Claude (brainstormed with Ron Santos)

## Problem

The Manual tab has accumulated context the Live run never got: per-case login
credentials, the QMetry precondition, case-level test data, and per-step test
data. A tester running a plan live sees only the bare step tape. Separately, when
watching a visible browser (`HEADLESS=false`) the agent begins acting the instant
Chromium appears — too fast to follow — and the screenshots it stores show page
pixels with no indication of which URL they were taken on.

## Scope

Four changes, all on the Live-run path:

1. Run-level login fields that fall back to the `.env` admin account.
2. Precondition, case test data, and per-step test data in the live tape
   (full parity with the Manual panel).
3. A configurable pause after each case's browser launches.
4. A URL banner composited onto every captured screenshot.

Out of scope: the per-action filmstrip UI (frames are still discarded after the
evaluator sees them), and any change to how results are pushed to QMetry.

## 1. Run-level credentials

`POST /runs` gains two optional body fields:

```json
{ "plan": "SOUSCLOUD-TR-482", "username": "…", "password": "…" }
```

The server holds them **in memory for the lifetime of that run only**. They are
never written to `run_state`, so `GET /runs/{id}` and the SSE stream never carry
them. This mirrors the established Manual rule: credentials travel inbound on
their own POST and never travel outbound.

`Orchestrator.run_plan` currently accepts only `plan_key`. It gains a
`credentials: tuple[str, str] | None = None` parameter and forwards it to
`_execute_case`, which already accepts and honors one.

### Precedence, highest first

1. Credentials saved for that specific case in the Manual tab
   (`manual_sessions/<plan>.json`, via `ManualStore`)
2. The run-level pair from `POST /runs`
3. The `.env` admin account

Resolution happens per case inside the run loop, so a plan that mixes roles works
without splitting it into separate runs.

### UI

One "Login as" username/password pair beside the **Run plan** button, reusing the
Manual styling and the existing helper copy *"Leave blank to use the system admin
account."* Blank fields send no credential fields at all.

## 2. Live-tape parity — run_state contract change

This changes the shared contract, so FRONTEND.md, `agent/run_state.py`, the
frontend hook and tape components, `fixtures/sample_run_state.json`, and
`tests/test_run_state.py` must all change together (CLAUDE.md rule).

| Field | Type | Owner |
|---|---|---|
| `precondition` | `str \| null` | `TestCase` |
| `test_data` | `[{name, value}]` | `TestCase` |
| `test_data` | `str \| null` | `Step` |

The orchestrator already receives all three from `QMetryCaseSource` on the
`Case`/`Step` objects it iterates; they are currently dropped when populating
`run_state`. No new QMetry calls are needed.

Rendering reuses the Manual panel's treatment, including the italic *none* when a
step carries no test data. Card order follows Manual: precondition → test data →
steps.

## 3. Launch delay

A new env var (default `3.0` seconds, `0` disables) applied in `_execute_case`
immediately after `open_session()` and before the first action of the case.

The delay is **skipped when `HEADLESS=true`**, because its only purpose is to let
a human follow a visible window, and on a 73-case cycle an unconditional 3s pause
adds ~3.5 minutes of wall clock. If Ron prefers it to fire unconditionally,
remove that condition — nothing else depends on it.

Tests inject `0` so the suite stays fast.

## 4. URL banner on screenshots

Playwright's `page.screenshot()` captures the page viewport only and cannot
include Chrome's real address bar, so the URL must be drawn in.

New module `agent/url_banner.py` exposing one pure function:

```python
def stamp_url(png_bytes: bytes, url: str) -> bytes
```

It returns a new PNG roughly 32px taller than the input, with the URL drawn in a
strip **above** the untouched page pixels. Width is unchanged and no page content
moves. Being pure and browser-free, it unit-tests without Playwright.

`BrowserSession.screenshot()` calls it with the existing `current_url()`. Because
every capture funnels through that one method, **all** frames get the banner —
the per-action frames sent to the evaluator and the final frame stored on the
step. The evaluator therefore gains URL evidence and can judge navigation steps
on what it sees rather than by inference. The orchestrator needs no change.

Adds `Pillow` to `requirements.txt`.

### Error handling

Font resolution tries a system TrueType (Arial on Windows, DejaVu elsewhere) and
falls back to PIL's built-in bitmap font. **If stamping raises for any reason the
raw screenshot is returned unmodified** — a cosmetic banner must never fail a
step, consistent with the existing "a lost frame never fails the step" rule in
`_execute_step`.

### Why not the alternatives

- *Injecting an overlay div into the page* needs no dependency and would also be
  read by the evaluator, but it mutates the DOM under test and covers the top of
  the page — unacceptable when the page is the thing being verified.
- *Captioning the URL beside the image in HTML* is risk-free but leaves the URL
  out of the PNG, which is exactly where it is wanted when pasting evidence into
  a bug report.

## Testing

- `stamp_url` returns a valid PNG, taller than the input, same width.
- `stamp_url` with malformed bytes returns the input unchanged (no raise).
- `BrowserSession.screenshot()` stamps using the current URL (mocked page).
- Launch delay is honored, and skipped at `0` and when headless.
- `POST /runs` accepts credentials; `GET /runs/{id}` never echoes them.
- Per-case Manual credentials take precedence over the run-level pair.
- `run_state` serialization matches FRONTEND.md, with fixture parity.

## Files touched

```
agent/url_banner.py          NEW — stamp_url
agent/browser.py             screenshot() composites the banner
agent/orchestrator.py        run_plan(credentials=…); launch delay in _execute_case;
                             precondition/test_data into run_state
agent/run_state.py           TestCase.precondition, TestCase.test_data, Step.test_data
server.py                    POST /runs body fields; in-memory per-run credentials;
                             per-case precedence against ManualStore
requirements.txt             + Pillow
FRONTEND.md                  contract + live-tape spec
fixtures/sample_run_state.json
frontend/src/…               live tape: login fields, precondition, test data
tests/…                      test_url_banner.py (new) + browser, orchestrator,
                             server, run_state
```
