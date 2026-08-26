# Run control + QMetry status — design

**Date:** 2026-08-26
**Status:** approved, not yet implemented
**Scope:** four features, shipped in the order below as four independent changes.

Four related changes to how a tester starts, stops, and reads a run:

| Order | Feature | Summary |
|---|---|---|
| 1 | Emergency stop | One button cancels every in-flight run. |
| 2 | Case selection | Per-case tickboxes on the Live run tab choose what runs. |
| 3 | Stop on failure | The first non-pass step ends the whole run. |
| 4 | QMetry status dots | The rail dot shows each case's QMetry execution result. |

The order is deliberate and is **not** the order they were requested in.
Feature 3 shipped before feature 2 would make the console worse: today a
73-case run completes and shows every failure, whereas stop-on-failure alone
would die at case 3 with no recovery but re-running all 73 from the top. The
tickboxes are what make stop-on-failure survivable, so they land first.

---

## 1. Emergency stop

### Behaviour

A single control cancels whatever is running — the Live-run plan, a Manual-tab
per-case agent run, or several at once — and does nothing else.

Deliberately **out of scope** (decided 2026-08-26): closing orphaned browsers,
clearing stranded `agent_status: "running"` marks, and any "re-arm before you
may run again" latch. The feature is a stop, not a safety interlock.

### Backend

`POST /stop` — no body.

```json
{ "cancelled": ["run-1a2b3c4d", "run-9f8e7d6c"] }
```

Cancels every task in `server.TASKS` that is not already `done()`, via the same
`task.cancel()` call the existing `POST /runs/{id}/cancel` uses. No new
cancellation semantics are introduced, which is the point: per-case `finally`
blocks and the existing manual-mark bookkeeping keep working unchanged.

Idempotent. Returns `200` with an empty list when nothing is running — never
`404`. A safety control that errors when you press it twice is not a safety
control.

### Frontend

A danger-styled button in the rail, below the plan progress block, present on
**both** tabs — a tester must not have to navigate to reach the brake. Enabled
only when something is in flight; the condition already exists in `App.jsx` as
`isRunning || Boolean(manualAgentRunning)`.

On success the button shows a transient confirmation and existing polling picks
up the cancelled state naturally. No optimistic local state — the server's view
of what is running stays authoritative.

### Known limitation

Without browser cleanup, cancelling a **headed** full-plan run mid-action can
leave a Chromium window open with nothing driving it. Manual-tab runs are always
headless (`server._build_orchestrator` forces `headless=True` on that path), so
they are unaffected. Accepted knowingly to keep the change minimal; revisit if it
becomes a nuisance in practice.

---

## 2. Case selection tickboxes

### Behaviour

Every case in an opened cycle carries a checkbox in the rail, **all ticked** when
the cycle opens — so pressing Run without touching anything behaves exactly as it
does today. Unticking excludes a case. The Run button reads `Run 3 of 4` and
disables when nothing is selected.

Live run tab only. The Manual tab already has its own per-step agent checkboxes
and gains nothing here.

### Frontend

`Rail.jsx`'s case row is currently a single `<button class="case-row">`. A
checkbox cannot be nested inside a button — invalid HTML, and it breaks both the
click target and keyboard focus, which the quality floor treats as
non-negotiable. So the row becomes:

```
.case-row-wrap  (flex)
├── <input type="checkbox">        ← only when `selectable`
└── <button class="case-row">      ← unchanged: dot + id + name
```

Rendered only when a new `selectable` prop is true. `Rail.jsx` is a single shared
instance serving both tabs (`App.jsx` swaps `railState` per tab), so this prop is
what keeps the Manual tab untouched.

`App.jsx` owns `selectedIds` as a `Set`, initialised to every case id whenever a
cycle's case list loads — including on a cycle change, so switching runs never
inherits a stale selection. A `Select all / none` toggle sits in the
`.rail-section-label` row with a live `3 of 4` count. Unticked rows render dimmed.

Checkboxes and the select-all toggle **disable while a run is in flight**, the
same rule the Run button and the global rail settings already follow. The set of
cases a run covers is fixed when it starts; letting it be edited mid-run would
imply the run picks up changes, which it cannot.

### Backend

`POST /runs` body gains an optional field:

```json
{ "plan": "SOUSCLOUD-TR-482", "case_ids": ["SOUSCLOUD-TC-2", "SOUSCLOUD-TC-9"] }
```

`case_ids` absent or `null` means every case — preserving the current contract
for the CLI and any existing caller. An empty list is a `422`: "select at least
one test case". Silently running nothing would look identical to a broken run.

`Orchestrator.run_plan` takes `case_ids: list[str] | None = None` and filters the
list returned by `case_source.list_cases` before the rail is pre-populated. Ids
in `case_ids` that are not in the plan are logged and ignored rather than raising
— a stale frontend selection should not fail the run.

### The run_state decision

Unselected cases are **excluded from run_state entirely**, not added with a
`skipped` status.

Adding `skipped` would mean a new `CaseStatus` value, which is a run_state
contract change rippling to FRONTEND.md, `agent/run_state.py`, both
`sample_run_state.json` fixtures, and the frontend hook. Excluding costs nothing
and keeps `summary.total` honest: select 3 of 4 and Total reads 3, so the
progress bar and pass/fail counts describe the run that actually happened.

Consequence to accept: a completed run's state contains no evidence of what was
deselected. The QMetry push already only writes cases present in the run, so
nothing is mismarked upstream.

---

## 3. Stop the whole run on the first non-pass step

### Behaviour

`RUN_MODE` has been documented in `CLAUDE.md` and `.env.example` since the
project started and is **implemented nowhere** — zero code references. This
feature is that flag finally wired up.

Under `RUN_MODE=stop_on_fail`:

- the first step resolving **`fail` or `blocked`** ends its case immediately —
  remaining steps in that case do not run;
- **that case takes the stopping step's own status** — a `fail` step yields a
  `fail` case, a `blocked` step a `blocked` case. This is the existing
  `fail > blocked > pass` precedence with only one non-pass step to consider, so
  no new resolution rule is needed;
- remaining cases **never start** and stay `queued` in run_state;
- the run transitions to `done`.

Both non-pass statuses stop the run, per the 2026-08-26 decision. Unstarted cases
stay `queued` rather than being marked skipped or blocked — run_state should not
claim an outcome for work that never happened.

`.env` is set to `stop_on_fail`. `continue` stays supported and documented as the
escape hatch; there is deliberately **no UI toggle**.

This partly reverses the 2026-07-07 decision that cases continue past failed
steps (outcome `fail > blocked > pass`). That behaviour is retained under
`RUN_MODE=continue`, which is what makes this a flag rather than a rewrite.

### Login failures

Covered with no extra work. `agent/login.py:100` raises `BrowserError` on a
rejected form, which resolves as a non-pass step and trips the same rule. A bad
password stops the run at case 1 instead of failing 73 cases identically.

### Implementation

`Orchestrator._execute_case` reports whether it hit a stopping condition;
`run_plan` breaks out of its case loop on that signal, then calls
`state.finish()` exactly as it does on normal completion. The signal is a return
value, not an exception — the loop's existing `except Exception` arm means a
sentinel exception would be caught and logged as a crashed case.

`run_single_case` needs no change: a single case has no subsequent cases to stop.

### Cost note

This is genuinely a trade-off, recorded because it was raised and overruled: a
single flaky step now ends a whole cycle, so an unattended regression sweep can
no longer be relied on to complete. `RUN_MODE=continue` is the one-line revert.

---

## 4. QMetry status dots

### Behaviour

Each case's dot in the rail shows its QMetry execution result — whether it has
been run, and how it went — using **QMetry's own colours**, so the console agrees
with what the tester sees on the QMetry site.

This is not a separate "history" concept layered onto the row. There is one
status signal: QMetry's result when no run is active, live run status once a run
starts. That is what removes the need for any second visual treatment (an earlier
draft proposed a coloured left edge; it is not needed and is not being built).

**Which tab:** the Live run tab only. The Manual tab's rail already shows each
case's *hand mark* in that dot (`App.jsx` maps `c.manual.status` into it), which
is a tester's own verdict and outranks what QMetry last recorded. Overwriting it
would hide the mark the tester just made. So the precedence is, per tab:

| Tab | Dot shows |
|---|---|
| Live run, no run started | QMetry `execution_result` |
| Live run, run active or done | live case status (`queued`/`running`/`pass`/`fail`/`blocked`) |
| Manual | the hand mark, unchanged by this feature |

### The field: `executionResult`

Verified live 2026-08-26 against cycle `1ZwYH2ObF7AGZa` and 8 further cycles.

`_CASE_FIELDS` becomes `key,summary,precondition,executionResult`. This is the
`fields`-is-load-bearing rule again, and it bites hard here: `executionStatus`,
`lastExecutionStatus`, `testCaseExecutionStatus`, and `status` were all
**silently ignored**, each returning `HTTP 200` with a row that simply lacked the
field. Only `executionResult` works. A wrong guess here would have shipped a
feature that always reads "not run" and never errors.

Response shape:

```json
"executionResult": {
  "id": 101543, "name": "Pass", "color": "#14892C", "isDefault": true,
  "description": "...", "defaultName": null, "seqNo": null, "autoStopTimer": null
}
```

The five result types in this project (`GET /projects/{id}/execution-results`):

| name | id | QMetry colour |
|---|---|---|
| Pass | 101543 | `#14892C` |
| Fail | 101540 | `#D04437` |
| Blocked | 101539 | `#CCC` |
| Work In Progress | 101541 | `#F6C342` |
| Not Executed | 101542 | `#205081` |

**A never-run case returns `"Not Executed"`, not a null.** Confirmed across
cycles: TR-491 is 92/92 Not Executed, TR-492 is a 56/36 Pass/Not-Executed mix.
The absent/null case is still handled defensively and treated as not-run, because
an unknown filter or field returns no error to warn us.

**Work In Progress appeared in zero of the 8 cycles probed.** It is rendered if
it ever appears and otherwise not designed around.

### Cost

Free. `executionResult` rides on the existing cycle case-search call — no extra
request, no added latency to the ~2s cycle open. It is cached alongside steps and
case test data so a list refresh does not drop it.

### Data flow

`QMetryCaseSource.list_cases` exposes a normalised field per case:

```json
"execution_result": { "name": "Pass", "color": "#14892C" }
```

`null` when absent. Only `name` and `color` are carried; the description and the
various always-null fields are dropped at the client boundary rather than
travelling to the frontend.

It surfaces through **manual session state**, which is also what feeds the Live
tab's preview (`App.jsx`'s `livePreview` is built from `manualState`). So:

- `agent/manual_state.py` — `ManualSession` case serialization gains the field
- `GET /manual/{plan}` — carries it
- **`run_state` is unchanged.** Confirming this is the point of leaving
  `test_run_state.py` untouched: the contract survived all four features intact.

### Colours

Introduce QMetry-result tokens in the `:root` block, valued at QMetry's hexes:

```css
--qm-pass:    #14892C;
--qm-fail:    #D04437;
--qm-blocked: #CCC;
--qm-wip:     #F6C342;
--qm-notrun:  transparent;   /* Not Executed — "clear" */
```

Mapped by result **name**, not by the API's `color` value. The API does return a
hex, but consuming it as an inline style would violate the "never hardcode a hex
inline / derive every colour from tokens" rule, and would let a QMetry admin's
config change silently repaint the console. Name to token is deterministic and
themeable; the API `color` is what these token values were read from and is
otherwise unused.

These sit alongside the existing `--green` / `--red` / `--amber` status tokens
rather than replacing them: those still serve live run status, which is a
different signal that happens to share a vocabulary. Both blocks are documented
in FRONTEND.md.

---

## Documentation to update

Both are one contract with the code and must change in the same commits:

- **FRONTEND.md** — `POST /runs` body (`case_ids`), `POST /stop`, the drilled-in
  rail state (checkboxes, selection count, status dots), the `:root` token block,
  and the Manual session state shape (`execution_result`).
- **CLAUDE.md** — `RUN_MODE` becomes implemented rather than aspirational;
  `_CASE_FIELDS` gains `executionResult`; the new endpoint joins the server's
  endpoint list.

`/stop` must also be added to `frontend/vite.config.js`. An unlisted prefix does
not 404 — it falls through to Vite's SPA fallback and returns `index.html`, so
the caller's `res.json()` fails on the leading `<`. This has bitten this project
before (`/cycles` and `/testcases`, fixed 2026-08-18).

## Testing

Mocked httpx and a mocked Playwright `Page` throughout — no network, no Chromium.

| Area | Cases |
|---|---|
| `test_server.py` | `POST /stop` cancels multiple in-flight tasks; returns `200` + empty list when idle; `case_ids` reaches the orchestrator; empty `case_ids` is `422`; absent `case_ids` runs everything |
| `test_orchestrator.py` | `case_ids` filters the case list; unknown ids ignored and logged; `stop_on_fail` halts on `fail`; halts on `blocked`; leaves later cases `queued`; `continue` preserves today's behaviour; `run_single_case` unaffected |
| `test_qmetry.py` | `executionResult` parsed to `{name, color}`; absent field gives `null`; `Not Executed` distinguished from `Blocked`; `_CASE_FIELDS` names the field |
| `test_manual_state.py` | `execution_result` round-trips through `ManualSession` and the on-disk snapshot; unknown-key tolerance still holds for old files |
| `test_run_state.py` | **unchanged** — the evidence the contract was not disturbed |

A green suite proves none of this broke the seams; it does not prove the QMetry
field still arrives. That needs one live cycle open, which is also the cheapest
check that `executionResult` has not been renamed upstream.
