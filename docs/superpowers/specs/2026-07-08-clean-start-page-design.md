# Clean start page — nothing loads until a test run is chosen

**Date:** 2026-07-08
**Status:** SHIPPED 2026-07-08 (build + 178 tests green; live acceptance via headless Chromium: clean start with zero /manual fetches, placeholder dropdown, choose-TR loads and rewrites URL, direct ?cycle= link unchanged)

## Problem

Opening the console with no `?cycle=` auto-loads `QMETRY_DEFAULT_CYCLE` and
immediately renders a full cycle. Roman wants a clean start: no cycle content
until he explicitly chooses a test run (TR).

## Behavior

- **No `?cycle=` in the URL:** do NOT fetch any manual session or preview.
  - Stage area (both tabs) shows a start panel: Duke shield, "QA Agent" title,
    "Choose a test run to begin", hint text pointing at the PLAN dropdown in
    the rail, an input to paste a cycle id/key (covers non-SOUSCLOUD cycles
    that aren't in `GET /cycles`) with an Open button, and — when `GET /config`
    returns a `default_cycle` — a secondary "Continue with <key>" button.
  - Rail: dropdown shows a disabled placeholder option "— choose test run —"
    selected; case list area shows nothing (no "No cases loaded." noise);
    progress shows 0/0.
  - Summary strip shows zeros (or is simply absent with the start panel).
- **Choosing a TR** (dropdown, paste+Open, or Continue) rewrites the URL to
  `?cycle=<idOrKey>` via the existing `handleSelectCycle` and loads exactly as
  today.
- **`?cycle=` present:** behavior byte-for-byte as today (bookmarks, links,
  cycle switching unchanged).

## Implementation

- `frontend/src/App.jsx`: `manualPlanKey = chosenCycle || cycleParam || null` —
  drop the `defaultCycle`/fixture fallback for AUTO-loading (the fallback chain
  for `planKey` display can stay for the Live-run plan label after a run
  starts). When `manualPlanKey` is null, render `<StartPanel …>` in the stage
  area instead of ManualView/Live view. `/config` is still fetched — it feeds
  the Continue button. `/cycles` still feeds the rail dropdown.
- New `frontend/src/components/StartPanel.jsx`: presentational; props
  `defaultCycle`, `onSelectCycle`. Local state for the paste field.
- `frontend/src/components/Rail.jsx`: when `currentCycle` is falsy, the select
  renders with value `""` and a disabled `<option value="">— choose test run —
  </option>`.
- `frontend/src/tokens.css`: `.start-panel` styles (centered, navy-soft card,
  consistent with existing manual styles).
- `FRONTEND.md`: document the start state under the Manual tab section.
- No backend changes. No run_state/manual contract changes. No fixtures
  change.

## Tests

- No JS test runner exists in the repo; the gate is `npm run build` + the
  Python suite staying green (nothing backend changes) + live verification:
  `/` shows the clean panel and issues NO `/manual/*` request until a TR is
  chosen; `/?cycle=daYoCqgmH49VMx` loads directly as before.

## Acceptance (live)

1. `http://127.0.0.1:8000/` → start panel, network tab shows only `/config` +
   `/cycles`, no `/manual/…` fetch.
2. Picking a TR from the dropdown → URL gains `?cycle=…`, cases load.
3. Pasting `jZYJHjkvCabDMD` and clicking Open → RFHU device cycle loads.
4. "Continue with <default>" → default cycle loads.
5. Direct link with `?cycle=` behaves as today.
