# FRONTEND.md — QA Agent Console

This is the design spec for the QA Agent's web frontend. The frontend is a live
execution console: a tester picks a test plan, presses Run, and watches the agent
execute each test case step-by-step in real time, with results streaming in.

The backend agent (see CLAUDE.md) does the work. This frontend is a thin, real-time
view over the agent's run — it reads run state and renders it. It does NOT contain
test logic.

---

## Design intent

**The signature element is the execution tape** — a live, scrolling log of test
steps as the agent runs them. A step appears with a spinner while executing, then
resolves to pass (green) or fail (red) with the AI's evaluation reason beneath it.
The tester watches the agent think. Everything else on the page stays quiet so the
tape is the one thing you look at.

**One test case at a time.** Not a dashboard of cards. The left rail lists test
cases; the right stage shows the single active case and its tape. This mirrors how
the agent actually runs — sequentially, one case at a time.

**Calm, not noisy.** This is an internal Duke tool, not a consumer app. Status
colors are desaturated so a screen of green doesn't shout and a single red is
immediately visible.

---

## Brand + design tokens

Duke Manufacturing brand: navy blue and white. The navy carries the identity; white
and off-white surfaces keep it clean. Define these as CSS custom properties on :root
and derive every color from them — never hardcode a hex inline.

```css
:root {
  /* Duke navy — primary brand */
  --navy:        #1B2A6B;   /* primary — rail background, primary buttons */
  --navy-bright: #2A3F8F;   /* hover state for navy surfaces */
  --navy-deep:   #131F4D;   /* pressed / deepest */
  --navy-soft:   #EEF1FA;   /* tint — active highlights, badges on white */
  --navy-line:   #DDE3F2;   /* navy-tinted borders */

  /* Surfaces */
  --paper:  #F7F8FC;   /* app background, tape background */
  --white:  #FFFFFF;   /* cards, stage */
  --ink:    #1A1D2E;   /* primary text */
  --muted:  #6A7290;   /* secondary text */
  --faint:  #9AA0B8;   /* tertiary / timestamps */
  --line:   #E7EAF3;   /* neutral borders */

  /* Status — desaturated on purpose */
  --green:      #1F9D6B;   --green-soft: #E6F5EE;
  --red:        #D8453E;   --red-soft:   #FBEAE9;
  --amber:      #C9881A;   --amber-soft: #FBF1DC;  /* BLOCKED state */
}
```

### Typography

The type split is meaningful and must be consistent:
- **DM Mono** — used everywhere a *machine* speaks: test case IDs, selectors,
  step detail (e.g. `click [data-test=save]`), timings, status codes, plan keys.
- **Inter** — used everywhere a *human* reads: labels, names, navigation, buttons,
  evaluation prose.

```css
--font: 'Inter', system-ui, sans-serif;
--mono: 'DM Mono', monospace;
```

Load both from Google Fonts. Do not substitute other families — the mono/sans split
is the typographic personality of the tool.

Type scale (approximate, px): stage title 16/600, section labels 11/600 uppercase
0.07em tracking, body 13–14/400–500, mono detail 11–12/400, big stat numbers 20/500
mono.

---

## Layout

Two panels, fixed-height console (use `100vh` in production, or a fixed height in
embedded contexts).

```
┌─────────────┬──────────────────────────────────────────┐
│  RAIL 280px │  STAGE (flex: 1)                          │
│             │  ┌────────────────────────────────────┐  │
│ Duke shield │  │ stage-head: ID · title · [Run plan]│  │
│ QA Agent    │  ├────────────────────────────────────┤  │
│             │  │ stat-strip: Total Pass Fail Elapsed│  │
│ PLAN META   │  ├────────────────────────────────────┤  │
│ TP-45       │  │                                    │  │
│ ▓▓▓░░ 60%   │  │  EXECUTION TAPE (scrolls)          │  │
│             │  │  ┌──────────────────────────────┐  │  │
│ TEST CASES  │  │  │ ⟳  Navigate to Inventory     │  │  │
│ ✓ IRHS-R-01 │  │  │    goto /inventory/recipes   │  │  │
│ ✓ IRHS-R-02 │  │  ├──────────────────────────────┤  │  │
│ ⟳ HSHU-01   │  │  │ ✓  Click "New recipe"        │  │  │
│ ░ MUHC-01   │  │  │    click [data-test=new]     │  │  │
│ ░ FWM-01    │  │  │    ▸ Form visible            │  │  │
│             │  │  └──────────────────────────────┘  │  │
│             │  ├────────────────────────────────────┤  │
│             │  │ foot: ● status  [report][to Jira]  │  │
│             │  └────────────────────────────────────┘  │
└─────────────┴──────────────────────────────────────────┘
```

Responsive: below 640px, stack vertically — rail on top (collapsed to a horizontal
scroll or capped-height list), stage below.

---

## Components

### 1. Rail (`.rail`) — navy
- **Brand block**: white rounded tile containing the Duke shield + "QA Agent" /
  "Sous Chef Cloud" subtitle. Use the actual Duke logo asset
  (`frontend/public/duke-logo.png`) rather than text when available. The tile is
  **landscape** (58x40), matching the artwork's ~1.5:1 shield — a square tile
  shrank it to an illegible smudge. The asset must have a **transparent**
  background; a version shipped with an opaque black field rendered the white
  tile as a black box.
The rail has **two states**: *browse* (nothing open, or a library test case open)
and *drilled in* (a test run open). The brand block is present in both.

#### Browse state — `<CaseBrowser>` (`.browser`)
QMetry holds ~430 test runs and ~2500 test cases. Neither list can be loaded
whole, so the rail browses one page at a time and search is server-side.

- **Mode picker** (`.browser-pick`): a `Browse` label + a single `<select>` with
  "Test runs" / "Test cases". A **select, not side-by-side tabs** — the two are
  alternatives, not things to compare, so one control gives one answer. Test runs
  is the default. Style the `option`s explicitly (`--ink` on `--white`): the popup
  is drawn by the OS and inherits nothing from the navy rail.
- **Search** (`.browser-search`): translucent-white on navy with a `⌕` icon on
  the left and, once there's text, a round `✕` clear button on the right. Use
  `type="text"`, **not** `type="search"` — Chrome's native search decoration
  draws an off-palette glyph that reads as a broken dropdown arrow. Below the
  empty field, a hint shows what's accepted (`e.g. delete recipe, or TC-2075`).
  Debounced 300ms, then re-queried server-side via `?q=` — it is **not** a filter
  over the loaded page, so a search covers the whole project. Switching TR↔TC
  clears the query.
- **Never show a count and a loading message at once.** "Loading…" beside
  `0 of 0` reads as a failed search. The empty-state line ("No test cases match
  …") appears only once a request has settled; while one is in flight the list
  shows a spinner and `Searching…`.

#### What search accepts
QMetry's own search is a single case-insensitive substring on one field, with no
AND and no wildcards, and it silently **ignores** filter keys it doesn't know
(an `and: [...]` filter returns the entire project — worse than an error). The
backend therefore handles three query shapes; the frontend just sends `?q=`:

| Query | Behaviour |
|---|---|
| `2075`, `TC-2075`, `tc 2075`, `SOUSCLOUD-TC-2075` | exact key lookup — a key never appears in a name, so a substring search would find nothing |
| `regression` | straight substring; exact totals, one call |
| `delete recipe` | terms ANDed, **order-independent**. `total` is exact unless `truncated` is set, which means the scan hit its cap — render it as `N of TOTAL+` |
- **Results** (`.browser-results`): each row (`.browser-row`) **stacks** the
  **full** key in mono (`SOUSCLOUD-TC-2075`, not a shortened `TC-2075` — testers
  refer to cases by the whole key) above the name, which is clamped to **two
  lines**. Not one line: run names share long prefixes ("Sous Chef Cloud
  vX.X.X — …"), so single-line truncation makes different runs read identically.
  Full `key — name` in the row's `title`. No status dot: neither runs nor library
  cases have a marking status until opened.
- **Foot** (`.browser-foot`): `N of TOTAL` in mono on the left, a `Load more`
  button on the right when more remain. Never silently truncate — the count is
  always visible.
- Picking a **TR** drills the rail in. Picking a **TC** opens that one case in
  the stage and **leaves the browser up**, so the tester can walk the list.

#### Drilled-in state (a test run is open)
- **Back link** (`.rail-back`): "← All test runs", returns to browse and clears
  the URL params.
- **Plan meta**: the run's human key (mono) and its real name — cycles *do* have
  a name; it arrives as `summary` when the query asks for the field. Below it a
  thin progress bar (white fill on translucent track) with `done / total` and `%`
  in mono.
- **Test case list**: each row = status dot + ID (mono) + name (truncated). Status
  dot states: `queued` (dashed border), `run` (pulsing white dot), `pass` (solid
  green ✓), `fail` (solid red ✕). Active row gets a translucent white background.

#### Catalogue endpoints
Both return the same page shape; only the array key differs.

```
GET /cycles?q=&start=0&limit=50    -> { cycles: [{id, key, name}], total, start, limit, next_start, truncated }
GET /testcases?q=&start=0&limit=50 -> { cases:  [{id, key, name, plan_key}], total, start, limit, next_start, truncated }
```

`plan_key` is the synthetic one-case plan (`TC:<key>`) the console opens a
library case with. Both return empty lists (total 0) in fixture mode.

**`Load more` must use `next_start`, never `items.length`.** A page can return
more rows than it yields — the server drops rows it won't show — so counting
kept rows drifts off QMetry's offset and silently skips records.

### 2. Stage head (`.stage-head`)
- Active test case ID in a navy-soft pill (mono) + the case name (Inter 16/600).
- **Run button**: primary navy. States: idle ("▶ Run plan"), running (inverts to
  white-on-navy-border, "⏸ Running…"), done ("▶ Run again"). Disabled while a run
  is in progress for other controls.
- **Login row** (`<CredentialsRow>`, shared with the Manual tab's per-case row —
  same "Login as" label, same `.manual-credentials` markup and classes): username
  + password fields beside the Run button. Both disable while a run is in progress
  (same rule as the Run button). Help copy: "Leave blank to use the system admin
  account. A case with its own login saved on the Manual tab uses that instead." —
  a case's own saved credentials (see "Case-level test data" below) always win
  over whatever is typed here. The password is held in React state only, sent on
  `POST /runs`, and never echoed back by `GET /runs/{id}`.

### 3. Stat strip (`.stat-strip`)
Four inline stats: Total, Passed (green number), Failed (red number), Elapsed (mono,
live-incrementing during a run). Numbers in mono 20/500, labels Inter 11/muted.

### 4. Execution tape (`.tape-wrap` + `.step`) — THE SIGNATURE
- Section label "Execution tape" with a hairline rule extending from it.
- Each step (`.step`) is a white card: a square status marker (left), the body
  (action in Inter, detail in mono, evaluation prose in a colored pill), and the
  timing (mono, right).
- Marker states: `run` = navy-tinted box with a spinning ring; `pass` = green-soft
  box with ✓; `fail` = red-soft box with ✕.
- **Animation**: each step animates in (fade + 6px rise, 0.3s). While executing it
  shows the spinner and `···` for time; on resolution it's replaced by the resolved
  card with the eval pill and real timing. Auto-scroll the tape to keep the newest
  step in view.
- Respect `prefers-reduced-motion`: disable the spinner animation and the rise,
  keep instant state changes.
- **Screenshot**: when a resolved step carries a `screenshot_b64` (base64 PNG of the
  page captured after the step ran), the card shows a mono "▼ Show screenshot" toggle
  beneath the eval pill. Clicking it expands an inline `<img>` (data-URL); clicking
  again collapses it. Steps still running, or steps with a null `screenshot_b64`
  (e.g. dry-run mode), show no toggle.

### 5. Stage foot (`.stage-foot`)
- **Status line**: a dot (idle gray / running navy-pulse / done green) + a sentence
  in the interface's voice: "Ready to run. Press Run plan to start." → "Running
  HSHU-01 — High-stock hold-unit flow" → "Run complete — 1 failure needs attention".
- **Action buttons** (right): "View report", "Log failures to Jira", and "Push
  results to QMetry". All three DISABLED during a run. "View report" enables when
  the run finishes. "Log failures to Jira" enables ONLY if there is at least one
  failure. This is the human-in-the-loop gate — the agent proposes, the tester
  approves the side-effectful write.
- **"Push results to QMetry"** — disabled during a run and until the run finishes.
  Clicking it asks the tester to choose **Current execution** or **New execution**
  (inline, two buttons + Cancel — no modal, no floating toast); the choice is sent
  as the push `mode` (`edit` = current, `create` = new). Choosing a target then
  ALWAYS asks a final confirmation naming the mode and its consequence (edit
  replaces the existing execution's results; create adds a new one) — the write
  only fires on confirm. Once confirmed, while pushing
  it shows a spinner and reads "Pushing…" (`aria-busy="true"`); the outcome (pushed
  / skipped / errors counts) appears inline in the foot, styled red on error, never
  a floating toast. It is the Live-tab equivalent of the Manual tab's push gate.

---

## Copy rules (interface voice)

- Buttons say what happens: "Run plan", "Run again", "Log failures to Jira" — not
  "Submit" or "Execute".
- Status sentences describe state plainly, no apology, no mood: "Run complete —
  1 failure needs attention." An empty tape says "No run yet. Press Run plan to start."
- Failures in the eval pill state what was expected vs what happened, in the tool's
  voice: "Expected confirmation dialog — none appeared. Button did not respond."
- Sentence case everywhere. Mono for anything the machine emits verbatim.

---

## How the frontend connects to the agent

The frontend is a real-time view over a run. The agent backend (Python, see
CLAUDE.md) exposes run state; the frontend renders it. There are two supported
wiring modes — build mode A first, it's simpler:

### Mode A — polling a run-state JSON (build this first)
The agent writes/serves a `run_state.json` that the frontend polls every ~500ms.
Shape:

```json
{
  "plan": { "key": "SOUSCLOUD-TP-45", "name": "Inventory · smoke test" },
  "status": "running",            // idle | running | done
  "elapsed_seconds": 12.4,
  "summary": { "total": 6, "passed": 2, "failed": 0 },
  "test_cases": [
    {
      "id": "IRHS-R-01",
      "name": "Create inventory recipe",
      "status": "pass",          // queued | running | pass | fail | blocked
      "precondition": "…",        // QMetry precondition, or null
      "test_data": [              // case-level QMetry parameter table; [] when none
        { "name": "User Role", "value": "Admin" }
      ],
      "steps": [
        {
          "action": "Navigate to Inventory module",
          "detail": "goto /inventory/recipes",
          "status": "pass",       // running | pass | fail | blocked
          "evaluation": "Recipe list page loaded",
          "duration_seconds": 1.2,
          "screenshot_b64": null, // base64 PNG of the page after the step, or null
          "test_data": null       // QMetry per-step testData, or null
        }
      ]
    }
  ]
}
```

The frontend treats this file as the single source of truth and re-renders on each
poll. The agent updates it after every step. Precondition and case test data render
in a non-scrolling `.tape-context` block ABOVE the execution tape — not inside
`.tape-wrap` — so they stay visible while the tape below auto-scrolls to the newest
step during a run; this is a deliberate divergence from the Manual panel (where
they scroll with the rest of the case), forced by the live tape's auto-scroll
behavior. Order is precondition → case test data → steps. A step with no test data
shows an italic *none*.

### Mode B — Server-Sent Events (later, for smoother streaming)
The agent runs a small FastAPI server exposing `GET /runs/{id}/stream` (SSE). Each
event is a step-resolved or status-change payload. The frontend subscribes and
appends to the tape as events arrive. Use this once Mode A works — it removes the
poll lag so steps appear the instant the agent resolves them.

### Endpoints the frontend calls (Mode B / control plane)
- `POST /runs` body `{ "plan": "SOUSCLOUD-TP-45", "username", "password" }` →
  starts a run, returns run id. `username`/`password` are optional and only sent
  when both are non-empty (the Live tab's login row); a half-filled pair is
  never sent, and the backend falls back to the `.env` admin account. A case
  with its own login saved on the Manual tab wins over the run-level pair.
- `GET /runs/{id}` → current run_state JSON (same shape as Mode A).
- `GET /runs/{id}/stream` → SSE stream of step/status events.
- `POST /runs/{id}/report` → triggers HTML report generation (the "View report" btn).
- `POST /runs/{id}/log-bugs` → creates Jira bugs for failed cases (the gated button).
- `POST /runs/{id}/push-qmetry` → `{pushed, skipped, errors}`; gated (409 unless
  QMetry configured and the run is done) — writes per-step results, explicit,
  never automatic.

The frontend NEVER calls QMetry, Jira, or Azure directly — all of that is the
backend's job. The frontend only talks to the agent's own server. This keeps all
credentials server-side and out of the browser.

---

## Manual session state (Manual tab)

The console has two tabs: **Manual** and **Live run**. Live run is the execution
tape above. Manual is a hand-testing checklist over the same cycle. It reads a
separate state object from the agent server.

### Clean start state

Nothing loads until the tester explicitly picks something — nothing
auto-loads, even when `GET /config` returns a `default_cycle`. With no `?cycle=`
/ `?tc=` in the URL and nothing chosen yet in-session, `App.jsx` has
`manualPlanKey = null`; then:

- The stage area shows `<StartPanel>` on **both** tabs instead of
  ManualView/Live run — Duke shield, "QA Agent" title, "Choose a test run to
  begin", a hint pointing at the rail's TR/TC search, a paste-cycle-id input +
  Open button, and — only when `default_cycle` is non-null — a "Continue with
  `<key>`" button.
- No `/manual/*` request is made (`useManualState` is a no-op when its plan
  key is `null` — it never falls back to a fixture).
- The rail sits in its browse state; progress and the case list belong to the
  drilled-in state and aren't rendered at all.
- `GET /config` fires (it feeds the Continue button), as does the first
  catalogue page.

Picking a run (rail row, paste + Open, or Continue) rewrites the URL to
`?cycle=<idOrKey>`; picking a library case writes `?tc=<case key>`. Both load
exactly as a bookmarked link would, and a direct link bypasses the start panel.

### Deferred steps

Fetching a case's steps costs one QMetry call **per case**, so a run's cases
arrive without them and each case carries `steps_loaded`. When the tester opens
a case with `steps_loaded: false`, `ManualView` calls
`GET /manual/{plan}/cases/{id}/steps` once (guarded by an in-flight set, since
every mark triggers a refresh), then refreshes. Until it resolves the stage
shows "Loading steps for `<id>`…" — never an empty step list, which would let
the tester start an agent run with nothing selected.

### Standalone (library) test cases

A `TC:`-prefixed plan holds exactly one case, opened straight from the project
library. It has no QMetry execution behind it, so `standalone: true` comes back
in the session and the console **omits** the push control rather than disabling
it, with the status line reading "Library test case — marks and agent runs stay
local, nothing is written to QMetry." Marking and agent runs work normally.
`POST /manual/{plan}/push-qmetry` on such a plan is a 409 server-side too.

`GET /manual/{plan}` returns:

```json
{
  "plan": { "key": "SOUSCLOUD-TP-45", "name": "Inventory · smoke test" },
  "qmetry_configured": false,
  "standalone": false,
  "cases": [
    {
      "id": "IRHS-R-01",
      "name": "Create inventory recipe",
      "steps": [{ "action": "…", "expected": "…", "test_data": "" }],
      "steps_loaded": true,
      "test_data": [{ "name": "User Role", "value": "Admin" }],
      "precondition": "",
      "manual": {
        "status": "unmarked",        // unmarked | pass | fail | blocked — set server-side: the agent run's verdict, or derived from step_marks when a human has ruled. Never set directly by the UI.
        "comment": "",
        "failed_steps": [],           // step indices marked fail — derived, kept for back-compat
        "step_marks": {},              // "<step index>": {status: pass|fail|blocked|skip, note, agent_status, overrode}
        "agent_status": null,         // null | running | pass | fail | blocked
        "agent_run_id": null,
        "agent_steps": null,          // step indices the last agent run covered; null = all
        "agent_note": "",             // latest agent-run summary (per-step verdicts + findings)
        "pushed_to_qmetry": false,
        "login_username": "",         // per-case agent login; "" = default admin
        "has_password": false        // a per-case password is saved server-side (never sent here)
      }
    }
  ],
  "summary": { "total": 3, "passed": 1, "failed": 1, "blocked": 0, "unmarked": 1, "pushed": 0 }
}
```

The QMetry execution id used to write results back is server-side only and never
appears in this payload.

### Endpoints the Manual tab calls
- `GET  /config` → `{ "default_cycle": "<idOrKey>" | null }` — the cycle the
  console opens when the URL has no `?cycle=` (from `QMETRY_DEFAULT_CYCLE`).
- `GET  /cycles?q=&start=&limit=` and `GET /testcases?q=&start=&limit=` → one
  page of the rail's catalogue (shapes above); empty in fixture mode.
- `GET  /manual/{plan}` → the state above. `{plan}` is a cycle id/key, or
  `TC:<case key>` for a single library case.
- `GET  /manual/{plan}/cases/{id}/steps` → the same case dict with its steps
  filled in and `steps_loaded: true`. Idempotent; 404 without a built session
  or for an unknown case.
- `POST /manual/{plan}/cases/{id}/mark` body `{status, comment, failed_steps}` → updated case.
  The UI now only uses this to save the overall Notes text — it always sends
  back the case's current (server-derived) `status` and `failed_steps`
  unchanged, never a status the tester picked directly.
- `POST /manual/{plan}/cases/{id}/steps/{step_index}/mark` body
  `{status: "pass"|"fail"|"blocked"|"skip", note?: string, agent_status?: string|null}`
  → updated case dict. `agent_status` is the AI chip's verdict for that step
  (the live tape's `chipByStep[i]?.status` if a run has produced one, else the
  persisted `step_marks[i].agent_status` from a prior mark) — the frontend
  supplies it. When `status` contradicts a non-null `agent_status`, `note` is
  required: a blank note on an override returns `422 "override requires a
  note"`. 404 for an unknown case or an out-of-range `step_index`. The case's
  `manual.status` is re-derived from all its step marks after every call:
  any `fail` → `fail`; else any `blocked` → `blocked`; else any `pass` →
  `pass`; else (no marks, or only `skip`) → `unmarked`.
- `POST /manual/{plan}/cases/{id}/run-agent` optional body `{ "steps": [0, 1] }`
  (step indices the agent should execute; omit to run all; empty list → 422) →
  `{run_id}`; tape subscribes via `GET /runs/{id}`.
- `POST /manual/{plan}/cases/{id}/credentials` body `{ "username", "password" }`
  → updated case dict. Per-case login the agent uses instead of the `.env`
  default; both empty clears back to the default. The password is never sent
  back to the browser — only `has_password` (a boolean) is.
- `POST /manual/{plan}/push-qmetry` → `{pushed, skipped, errors}`; gated (409 if QMetry
  not configured or nothing marked). This is the human-in-the-loop write gate, like
  "Log failures to Jira" on the Live tab.
- `POST /runs/{run_id}/cancel` → `{"cancelled": true}` (404 if the run is unknown or
  already finished). Used by the Manual tab's per-case agent run (see "Cancelling a
  run" below); the Live tab does not use it.

### Case-level test data (`.manual-casedata`)
Below the precondition, a case shows its own **Test data** — QMetry surfaces a
parameterised case's parameter table under that name, as `name = value` pairs
(`User Role = Admin`, `Menu = Recipe`). These are the values the steps' `[~id]`
placeholders resolve to, so the block is what makes a *shared* step ("log in as
X") legible for this particular case.

Unlike the per-step field, the block is **omitted entirely** when a case has no
parameters (most don't) rather than showing "none" — there's no ambiguity to
resolve at case level. It arrives with the steps, so it's empty until they
hydrate.

### Step display and verdicts
- Each step shows, in order: the action, the expected result, its **test data**,
  and the agent's verdict.
- **Test data** (`.manual-step-data`) is per step — QMetry has a case-level
  `testData` field but it is always null, so the value comes from each step. The
  label is shown on **every** step, with an italic `none` when there isn't any: a
  blank row would leave a tester unable to tell "nothing to enter" from "didn't
  load".
- **There are no Pass / Fail / Blocked / Skip buttons.** The agent's verdict is
  the indication that matters, so each step renders a read-only
  `agent: pass` / `agent: fail` chip (`.step-verdict`) with the evaluator's
  reason beside it. The verdict comes from the live run's tape when there is one,
  falling back to `step_marks[i].agent_status` so it survives a refresh.
- The case header shows a **read-only** status pill. With no mark buttons, that
  status comes from the agent's own verdict — `ManualStore.set_agent` writes it
  onto the case when a run finishes `pass`/`fail`/`blocked`. Any pre-existing
  hand step-mark still wins (a human ruling outranks the AI), and a run still in
  flight is not a verdict.
- An overall Notes textarea is always visible under the step list; it saves
  on blur via `POST .../mark`, sending the case's current status back unchanged —
  the note is the only thing that endpoint still sets from the UI.
- **The "Agent notes" panel is not rendered.** It restated every step's verdict
  as one long wall of text directly under the steps that already showed them.
  `agent_note` is still recorded server-side and still goes into the QMetry
  comment on push — only the on-screen block is gone.
- "Push results to QMetry" is disabled during an agent run, when nothing is marked,
  and when `qmetry_configured` is false (shows "Connect QMetry to push results").
  Since the agent verdict now sets the status, finishing a run is what unlocks it.
- Each step also has an "agent" checkbox (all checked by default). "Run selected
  steps with agent" executes only the checked steps in a fresh browser session; a
  muted hint reads "The agent starts from the dashboard after login — do unchecked
  earlier steps by hand first."
- The agent-run tape below the case shows the executed steps only. It does **not**
  repeat a verdict row — every step in the list above already carries its chip,
  and showing both was duplicate UI.
- Credentials row carries helper copy — "Leave blank to use the system admin
  account." — under the Login-as inputs, stating the existing default-admin
  fallback (empty/cleared credentials already run as the `.env` admin).

### Cancelling a run (Manual tab)
- While a case's agent run is in flight, the "Run selected steps with agent"
  button is disabled, shows a spinner, and reads "Agent running…"
  (`aria-busy="true"`). A **Cancel** button appears beside it.
- Cancel calls `POST /runs/{run_id}/cancel` for that case's run, then refreshes
  the manual session. A cancelled run clears `agent_status` (back to `null`,
  which re-enables Run/Push) and leaves an `agent_note` ending "…cancelled by
  tester". Errors from either Run or Cancel surface in the same inline slot
  beside the buttons — not a floating toast.
- The case header (`id` + title + Run + Cancel) wraps at narrow widths so the
  buttons are never clipped off-screen.

### Push busy state + no overlapping toast
- "Push results to QMetry" first asks the tester to choose **Current execution**
  or **New execution** (inline, two buttons + Cancel — no modal, no floating
  toast); the choice is sent as the push `mode` (`edit` = current, `create` =
  new). Choosing a target then ALWAYS asks a final confirmation naming the mode
  and its consequence; the write only fires on confirm. Once confirmed, it shows
  a spinner and reads "Pushing…"
  (`aria-busy="true"`) for the duration of the push; the existing disabled/gated
  states (see "Marking UX" above) are unchanged, just now visibly communicated.
- The push result or error message renders inline in the footer's status line
  (styled red on error), not as a separate floating banner. A previous
  implementation additionally floated a fixed-position toast over the push
  button on load errors — that has been removed; the inline "Could not load
  cycle…" message above the case panel is the only place a load error appears.

---

## Tech

- **React** (functional components + hooks) or plain HTML/CSS/JS — either is fine.
  If React: Vite scaffold. Keep it a single small app, no heavy UI framework.
- **No CSS framework** — hand-write CSS using the tokens above. The design is
  specific; Tailwind defaults would erase it.
- **No browser storage** for run data — run state lives on the server. The frontend
  is stateless beyond the current poll/stream.
- Fonts: DM Mono + Inter from Google Fonts.

---

## Quality floor

- Responsive to mobile (stack at 640px).
- Visible keyboard focus on all interactive elements (Run button, test case rows,
  foot buttons).
- `prefers-reduced-motion` respected (no spinner spin, no rise-in).
- Disabled states are visually obvious (reduced opacity + not-allowed cursor).
- The gated "Log failures to Jira" button must be impossible to click during a run
  or when there are zero failures.

---

## File layout (frontend)

```
frontend/
├── public/
│   └── duke-logo.png         ← the Duke shield asset
├── src/
│   ├── App.jsx               ← console shell (rail + stage)
│   ├── components/
│   │   ├── Rail.jsx          ← brand, plan meta, test case list
│   │   ├── StatStrip.jsx     ← total/pass/fail/elapsed
│   │   ├── ExecutionTape.jsx ← the signature — step cards
│   │   ├── Step.jsx          ← one step card with marker + eval
│   │   └── StageFoot.jsx     ← status line + gated action buttons
│   ├── hooks/
│   │   └── useRunState.js    ← polls run_state.json (Mode A) / SSE (Mode B)
│   ├── tokens.css            ← the :root design tokens above
│   └── main.jsx
├── index.html
└── package.json
```
