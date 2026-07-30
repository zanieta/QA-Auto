# Cancel agent runs + push feedback + Manual-tab polish

**Date:** 2026-07-08
**Status:** SHIPPED 2026-07-08 (181 tests + build green; live: cancel mid-run
→ {"cancelled":true}, mark cleared with "cancelled by tester" note, no
chromium orphans; 800px viewport: run button fully visible, zero floating
toasts, push button inside footer, admin-fallback helper copy present.
NOT live-tested: an actual QMetry push — that writes real results; the busy
state is code/unit verified.)

## 1. Cancel a running agent run

**Backend (`server.py`):**
- `POST /runs/{run_id}/cancel`: `task = TASKS.get(run_id)`; 404 when unknown
  or already done (`task.done()`); else `task.cancel()`; return
  `{"cancelled": true}`.
- `_run_agent_case`: new `except asyncio.CancelledError:` branch BEFORE the
  generic `except Exception`: `state.finish()`, fan out the update, set the
  mark via `MANUAL.set_agent(plan, case_id, None, run_id,
  agent_note=f"Agent run {when} ({run_id}): cancelled by tester")`, then
  `raise` (the task must end cancelled). `agent_status=None` clears the
  "running" state so the Run/Push buttons re-enable and the chip disappears.
- Browser cleanup: `_execute_case`'s existing `finally` closes the session;
  acceptance verifies no orphaned Chromium.

**Frontend:**
- `useManualState.js`: `cancelRun(runId)` helper → POST the endpoint.
- `ManualCase.jsx`: while the case's run is in flight (`agentRunning`), the
  Run button reads "Agent running…" with a spinner (disabled, `aria-busy`),
  and a **Cancel** button appears beside it; clicking it calls
  `cancelRun(m.agent_run_id)` then `onChanged?.()`. Errors surface in the
  existing `runErr` slot.

## 2. Push button feedback + no overlap

- `ManualView.jsx`: REMOVE the floating duplicate toast (`{error && …}` after
  the footer — it renders on top of the push button; the load-failure path
  already shows the same error inline). Push result/error message stays in the
  footer `status-line`; give it an `error` modifier class when the push threw.
- Push button: `pushing` → spinner + "Pushing…", `aria-busy`; disabled during
  agent runs with the existing tooltip (unchanged logic, now visually
  communicated).

## 3. Polish pass (existing layout kept)

- `.manual-case-head`: `flex-wrap: wrap` + row gap so the Run/Cancel buttons
  never overflow off-screen at narrow widths.
- Shared `.btn` transitions (background/transform on hover/active) and a
  `.spinner` inline element (CSS border animation); both wrapped in
  `@media (prefers-reduced-motion: reduce)` overrides (FRONTEND.md rule).
- `.stage-foot`: flex with gap; `status-line` may wrap; long messages must
  never overlap the button.
- Credentials row helper copy under/beside "Login as":
  "Leave blank to use the system admin account." (This is already the backend
  behavior — empty credentials → .env admin; the copy just states it.)
- FRONTEND.md: document the cancel button and the push busy state in the
  gated-actions section; note the removed floating toast.

## 4. Default admin fallback

No behavior change — empty/cleared credentials already run as the `.env`
admin. Covered by the helper copy above.

## Tests

- `test_server.py`: cancel endpoint — 404 unknown run; cancel of a live
  (stubbed, long-sleeping) task returns `{"cancelled": true}` and the task
  ends cancelled; `_run_agent_case` cancellation path sets `agent_status`
  None + "cancelled by tester" note and re-raises CancelledError.
- Frontend: no JS runner — gate is `npm run build` + live acceptance.

## Acceptance (live)

1. Start an agent run on a case → Run button shows spinner "Agent running…",
   Cancel appears, Push is disabled with tooltip.
2. Click Cancel mid-run → run stops within a few seconds; chip/running state
   clears; agent note says "cancelled by tester"; no chromium.exe lingers
   (process list) ; Run + Push re-enable.
3. Push results → button shows "Pushing…" spinner; result message appears in
   the footer without overlapping the button; simulate an error (bad plan) →
   error message inline, still no overlap.
4. Narrow window (~800px): case header wraps; Run button fully visible.
