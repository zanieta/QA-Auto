# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This project is a QA automation agent **plus a live web console** for Duke
Manufacturing's Sous Chef Cloud testing.

The agent:
1. Reads test cases from QMetry (REST API)
2. Translates plain-English test steps to Playwright browser actions using Azure AI (GPT-4o)
3. Executes those steps against Sous Chef Cloud
4. Evaluates screenshots against expected results using GPT-4o vision
5. Writes PASS/FAIL/BLOCKED results back to QMetry
6. Auto-creates Jira bugs for failed test cases (gated — see below)

The console (frontend) is a real-time view over a run: a tester picks a plan,
presses Run, and watches each test case execute step-by-step. **The frontend design
is fully specified in `FRONTEND.md` — read that file before building or changing any
UI. Do not invent a different design.**

---

## Claude Code model policy

Route by task difficulty; keep the top model in the main loop for judgment and
delegate execution to cheaper tiers:

- **Haiku 4.5** — trivial mechanical work: bulk edits, formatting, running known
  commands, obvious one-line fixes.
- **Sonnet 5** — default implementation: real code, straightforward features,
  routine debugging with a known cause. Dispatch as subagents (`model: "sonnet"`
  on Agent/Workflow calls).
- **Opus 4.8+** — planning, architecture calls, hard/unknown-cause debugging,
  code review, brainstorming, analysis.

**Override by difficulty:** an easy step inside a hard task can drop a tier; a
hard step inside an easy task climbs one — route the step, not the label
("coding"). Integration-touching or cross-cutting reviews go to Opus, not Sonnet.

**Enforcement pattern:** Opus is the main orchestrator, delegating substantial
coding to Sonnet subagents — but does small, fully-specified edits (sub-~10 lines)
inline rather than paying subagent dispatch overhead.

**Fable 5** is unevaluated for this split (open item); until measured, treat it
as an Opus-tier main-loop model.

---

## Two parts, one repo

```
qa-agent/
├── CLAUDE.md                   ← you are here (backend + how it serves the frontend)
├── FRONTEND.md                 ← the frontend design system + component spec (authoritative for UI)
├── .env                        ← secrets (never commit)
├── .env.example
├── requirements.txt
│
├── main.py                     ← CLI entry point — run a plan headless
├── server.py                   ← FastAPI server — starts runs + serves run state to the frontend
│
├── .venv/                      ← Python 3.14 virtualenv (gitignored)
│
├── agent/                      ← the backend agent (Python, async)
│   ├── __init__.py
│   ├── orchestrator.py         ← main agent loop; writes run_state after every step
│   ├── run_state.py            ← the RunState model + JSON serialization (shared with frontend)
│   ├── case_source.py          ← CaseSource protocol + FixtureCaseSource (decouples orchestrator from QMetry)
│   ├── qmetry.py               ← QMetry API client (LIVE — works against qtmcloud.qmetry.com)
│   ├── jira_client.py          ← Jira API client + bugs_from_failed_run helper
│   ├── azure_ai.py             ← Azure AI (GPT-4o) client
│   ├── browser.py              ← Playwright execution engine
│   └── reporter.py             ← HTML summary report generator
│
├── prompts/
│   ├── step_translator.txt     ← English step → Playwright action JSON
│   └── result_evaluator.txt    ← screenshot + expected → PASS/FAIL JSON
│
├── frontend/                   ← the console UI (see FRONTEND.md for everything)
│   ├── public/
│   │   ├── duke-logo.png
│   │   └── fixtures/sample_run_state.json   ← Vite serves this at /fixtures/* in dev mode
│   └── src/…                   ← React + Vite app; talks ONLY to server.py
│
├── tests/                      ← pytest; httpx + Playwright Page mocked
│   ├── test_qmetry.py          test_azure_ai.py    test_browser.py
│   ├── test_jira_client.py     test_orchestrator.py test_reporter.py
│   ├── test_run_state.py       ← asserts JSON shape matches FRONTEND.md + fixture parity
│   └── test_server.py
│
├── fixtures/
│   ├── sample_plan.json        ← orchestrator INPUT — read by FixtureCaseSource
│   ├── sample_test_case.json   ← one test case in QMetry's source shape (reference)
│   └── sample_run_state.json   ← orchestrator OUTPUT — lets the frontend work pre-backend
│
├── scripts/
│   └── qmetry_probe.py         ← dev tool for discovering QMetry endpoint shape
│
└── reports/
    └── .gitkeep                ← reporter writes run_<ts>.html here
```

---

## Environment variables

All secrets in `.env`. Never hardcode. Never commit `.env`. The frontend gets NO
secrets — it only talks to `server.py`, which holds all credentials server-side.

```
# Azure AI
AZURE_AI_ENDPOINT=https://<your-project>.openai.azure.com/
AZURE_AI_API_KEY=<your-azure-ai-key>
AZURE_AI_DEPLOYMENT=gpt-4o
AZURE_AI_TRANSLATOR_DEPLOYMENT=   # optional; cheap text model for step translation
AZURE_AI_EVALUATOR_DEPLOYMENT=    # optional; vision model for screenshot evaluation
                                  # both fall back to AZURE_AI_DEPLOYMENT
EVALUATOR_PROMPT_FILE=            # optional; which prompts/*.txt the evaluator
                                  # loads. Defaults to result_evaluator.txt
                                  # (gpt-4o-tuned) when unset.

# QMetry
QMETRY_BASE_URL=https://dukemanufacturing.atlassian.net
QMETRY_API_KEY=<your-qmetry-api-key>

# Jira (Atlassian)
JIRA_BASE_URL=https://dukemanufacturing.atlassian.net
JIRA_EMAIL=<your-atlassian-email>
JIRA_API_TOKEN=<your-atlassian-api-token>
JIRA_PROJECT_KEY=SOUSCLOUD
JIRA_BUG_ISSUE_TYPE=Bug

# Target app
APP_BASE_URL=https://<sous-chef-cloud-uat-url>
APP_USERNAME=<test-user-email>
APP_PASSWORD=<test-user-password>

# Server
SERVER_HOST=127.0.0.1
SERVER_PORT=8000
FRONTEND_ORIGIN=http://localhost:5173   # for CORS

# Behaviour flags
HEADLESS=true
SCREENSHOT_ON_PASS=false
AUTO_CREATE_BUGS=false          # START false; enable only after a verified run
RUN_MODE=continue               # continue | stop_on_fail
LOG_LEVEL=INFO
AGENT_LAUNCH_DELAY_S=3          # pause before a case's first action so a human
                                # watching a visible window can follow along;
                                # 0 disables; skipped entirely when HEADLESS=true
STEP_MAX_ATTEMPTS=3             # retries per step (re-snapshot + re-translate)
                                # before escalating a non-pass status
EVAL_MAX_FRAMES=8               # frames sent to the vision evaluator per step.
                                # Base64 PNGs dominate evaluation token cost, so
                                # this is the LARGEST cost lever in a run —
                                # bigger than the model tier. Keeps the LAST n
                                # frames, so the settled end state always ships.
                                # Below 1 is clamped to 1.
```

---

## The shared contract: run_state.json

This is the single most important interface in the project. The backend produces it;
the frontend consumes it. **The shape is defined in FRONTEND.md under "How the
frontend connects to the agent" and must match exactly.** `agent/run_state.py`
owns this model; `test_run_state.py` asserts the serialized shape.

The orchestrator updates run state after **every step** so the frontend tape updates
in near-real-time. Step status values: `running | pass | fail | blocked`. Test case
status values: `queued | running | pass | fail | blocked`. Run status: `idle |
running | done`.

If you change the run_state shape, you MUST update FRONTEND.md and the frontend
hook in the same change. They are one contract.

---

## Backend commands

The Python env lives in `.venv/` at the repo root (Python 3.14). Always invoke it
via `.venv/Scripts/python.exe` (Windows) so you don't accidentally pick up a
system Python. The venv's `pip.ini` and `frontend/.npmrc` already bypass Duke's
corporate SSL inspection — don't add `--trusted-host` / `--strict-ssl=false`
flags by hand.

```powershell
# First-time install (or after editing requirements.txt)
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium

# Run a plan headless from the CLI (no frontend)
.venv\Scripts\python.exe main.py --plan SOUSCLOUD-TP-45

# Single case / dry run for debugging
.venv\Scripts\python.exe main.py --testcase IRHS-R-01 --dry-run
$env:HEADLESS="false"; .venv\Scripts\python.exe main.py --testcase IRHS-R-01

# Start the server (frontend talks to this)
.venv\Scripts\python.exe server.py     # SERVER_PORT, exposes run API + run_state

# Tests
.venv\Scripts\python.exe -m pytest tests/ -q                                 # full suite
.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v             # one module
.venv\Scripts\python.exe -m pytest tests/test_azure_ai.py::test_retries_on_429_then_succeeds -v   # one test
```

Tests use mocked httpx + a mocked Playwright Page — they never hit the network or
launch Chromium. A green suite is not proof that real Azure / Playwright work;
that's what `main.py --dry-run` is for.

---

## Frontend commands

```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173, proxies API calls to server.py on :8000
npm run build      # production build → frontend/dist
```

For local dev: run `python server.py` in one terminal and `npm run dev` in another.
The Vite dev server proxies `/runs/*` to the backend so there are no CORS issues in
dev. In production, `server.py` serves the built `frontend/dist` as static files.

---

## Backend modules — what each does

### agent/run_state.py
Defines the `RunState` model and its `.to_dict()` → JSON matching FRONTEND.md
exactly. Single source of truth for the frontend contract. Helpers: `start_run()`,
`start_case(id)`, `add_step(...)`, `resolve_step(status, evaluation, duration)`,
`resolve_case(status)`, `finish()`. The orchestrator calls these; the server
serializes current state on each request.

### agent/manual_state.py
The Manual-tab contract. `ManualStore` holds per-case hand marks
(pass/fail/blocked + note + flagged failing steps + any per-case agent run),
keyed by plan and snapshotted to `manual_sessions/<plan>.json`. `ManualSession`
serializes to the shape in FRONTEND.md's "Manual session state". `compose_comment`
builds the QMetry comment from the note + flagged steps. The QMetry execution id is
held server-side only.

### agent/case_source.py
The seam between the orchestrator and where test plans come from. Defines a
`CaseSource` protocol (`get_plan`, `list_cases`) and ships one implementation,
`FixtureCaseSource`, which reads `fixtures/sample_plan.json`. The orchestrator
takes a `CaseSource` in its constructor — never a `QMetryClient` directly — so
swapping fixtures for QMetry is a one-line change in `server.py` once the QMetry
shape is known.

### agent/qmetry.py
QMetry REST API client. **Status: implemented and working against the LIVE API**
(verified 2026-06-30 against cycle `1ZwYH2ObF7AGZa` / `SOUSCLOUD-TR-482`).
- **Host:** `https://qtmcloud.qmetry.com`, base `/rest/api/latest` (NOT
  `dukemanufacturing.atlassian.net`, NOT `/rest/qtm4j/v2`).
- **Auth:** `apiKey: <key>` request header (from QMetry → Configuration → Open API).
- **`fields` is load-bearing.** `summary` (the name) and `precondition` are
  omitted from every response unless the query names them explicitly —
  `fields=all` does NOT work. Asking for them is what lets a whole cycle's case
  list load in one call. Constants: `_CASE_FIELDS`, `_CYCLE_FIELDS`.
- Real response shapes (these differ from the published spec — they were
  reverse-engineered from live calls):
  - `get_test_cycle` → cycle is wrapped under `data`. Cycles **do** have a name:
    it's `summary`, and it arrives with `?fields=key,summary,description`
    (corrected 2026-08-04 — the earlier "no cycle name field" note was just a
    missing `fields` param).
  - `search_test_cases` → body must be `{"filter": {}}`; rows carry
    `testCaseExecutionId` + `versionNo`, plus `summary`/`precondition` when
    `fields` asks. No per-case version-detail call is needed any more.
  - `search_test_cycles` / `search_project_test_cases` → paged catalogue searches.
    Cycle search also takes `filter.archived: false`. Both return
    `{total, rows, page_size, truncated}`; `page_size` is the raw row count so
    callers page by the server's offset, not by rows kept.
  - **Search is one substring on one field.** No AND, no wildcards, and unknown
    filter keys are *silently ignored* — an `and: [...]` filter happily returns
    the whole project, so never assume a filter worked because it didn't 400.
    `filter.key` matches only a **complete** key (`SOUSCLOUD-TC-2075`; `TC-2075`
    returns nothing). `_search_catalogue` therefore handles three query shapes:
    a key-ish query (expanded to the full key via `QMETRY_PROJECT_KEY` /
    `JIRA_PROJECT_KEY`), a single term (straight substring), and several terms
    (probe each term's count, scan the rarest, AND the rest locally). Multi-term
    search is capped at `_MAX_SCAN_PAGES`; `truncated` says `total` is a floor.
  - `get_test_steps` → body `{}`; some steps are *shareable* (real steps nested
    under `shareable.shareableTestSteps`) and are flattened by `_load_steps`.
  - `post_execution_result` → PUT to `/testcycles/{internal cycle id}/...` (the
    `data.id`, NOT the plan key).
`QMetryCaseSource` wraps the client; `server.py` auto-selects it when
`QMETRY_API_KEY` is set (and isn't the `REPLACE_WITH…` placeholder), else
`FixtureCaseSource`.

`list_cases(plan_key, with_steps=True)`: steps cost one call per case, so the
console asks for `with_steps=False` (one call for the whole run) and hydrates the
opened case via `get_case_steps`. Cases carry `_steps_loaded`. `run_plan` keeps
the eager default; `run_single_case` uses the cheap list + one hydrate.

A plan key of `TC:<case key>` (`standalone_plan_key` / `is_standalone_plan`) is a
synthetic one-case plan for a test case opened straight from the project library.
It has no cycle and no execution id, so its results are never pushed to QMetry.

### agent/jira_client.py
Jira REST API v3. Basic Auth = base64(`email:api_token`). Methods: `create_bug`,
`add_comment`, `attach_file`. Only called when AUTO_CREATE_BUGS=true OR the frontend
"Log failures to Jira" button fires `POST /runs/{id}/log-bugs`.

### agent/azure_ai.py
Azure OpenAI GPT-4o wrapper. Methods: `translate_step(step_text, app_context)` →
list of `{action, selector, value}`; `evaluate_result(screenshot_b64, expected)` →
`{status, reason}`. Prompts load from `/prompts/` — never inline them. Vision input
goes as a base64 `image_url` content block. `translate_step` takes the page element snapshot and biases output to choose a
target by `ref`. The orchestrator snapshots before translating and re-snapshots +
re-translates + retries a step once on a browser action failure (DOM-grounded
actions — see the 2026-06-30 spec).

**Evaluator model: gpt-4.1 (migrated 2026-08-13, measured).** `gpt-4o` is
deprecated in Azure, so `AZURE_AI_EVALUATOR_DEPLOYMENT=gpt-4.1` with the
**unmodified** `prompts/result_evaluator.txt`. gpt-4o remains deployed, so the
revert is one env var.

The migration was decided by measurement, not by reading model cards.
`scripts/prompt_eval/compare_combinations.py` judges one captured input N times
per (deployment × prompt file) combination and reports verdict distribution,
**flip rate** (same input, different verdicts across identical runs — the
failure mode that disqualified a mini-tier evaluator in 2026-07), disagreement
vs. a nominated baseline, and the reason strings. Result on
`eval_input_tc2_step4.json`, N=5:

| combination | verdicts | flip rate | vs baseline |
|---|---|---|---|
| `gpt-4o` × `result_evaluator.txt` (baseline) | pass 5/5 | 0% | — |
| `gpt-4.1` × `result_evaluator.txt` | pass 5/5 | 0% | 0% |
| `gpt-4.1` × `result_evaluator_41.txt` | pass 4, fail 1 | **20%** | 20% |

**The three-combination design is the point:** the middle row isolates the model
change from the prompt change. Without it, the third row's regression would have
been misattributed to gpt-4.1 when it was caused by the prompt edit.

`prompts/result_evaluator_41.txt` is **DISQUALIFIED** and kept only as the
record of that negative result (it carries a header saying so; a few
`tests/test_azure_ai.py` cases use it as the override fixture). Its additions —
explicit rule precedence, bounding the doubt rule — were meant to stop a literal
instruction-follower over-producing `fail`, and did the opposite: they added
non-determinism, and the failing run cited insufficient evidence, which that
file's own precedence block says must route to `blocked`. gpt-4.1 needs no
prompt changes.

`EVALUATOR_PROMPT_FILE` selects the evaluator's prompt file (default
`result_evaluator.txt`; behaviour unchanged when unset). Any future candidate
prompt or model goes through the harness above before either env var is flipped.
**Coverage caveat:** the decision rests on ONE captured step, and an easy one.
Capture the hard case (TC-2 step 3 — a 20-item multi-frame checklist) and a
should-obviously-fail control with `capture_eval_inputs.py`, and re-run the
comparison, before treating the migration as fully proven.

### agent/browser.py
Playwright async wrapper. Methods: `open_session`, `execute_action`, `screenshot`
(returns base64 PNG), `close_session`. Supported actions: `navigate, click, fill,
select, wait, assert_text, assert_visible, login, logout` (`login`/`logout` are
harness-executed: credentials and session mechanics never reach the model;
`logout` clears cookies and lands on the login page — used when a step expects
a logged-out state, see the RECONCILE rule in `prompts/step_translator.txt`
and the 2026-07-07 spec). Always close the session in a finally
block. `snapshot_elements()` tags visible interactive elements with `data-agent-ref` and
returns `{ref, tag, role, name}`; actions may carry a `ref` (resolved to
`[data-agent-ref="…"]`) so the model targets real elements instead of guessing CSS.
`snapshot_table_data()` (2026-08-13) returns the first visible on-page `<table>`
as `{headers, rows}` — real cell text, not just interactive elements — for
steps that need to see values already on screen (e.g. a Users table's email
column). Hard-capped in Python (`MAX_TABLE_ROWS`=15, `MAX_TABLE_COLS`=6,
`MAX_TABLE_CELL_CHARS`=40, truncating with an ellipsis) regardless of what the
page/JS returns, so it can never bloat a prompt or a log line; empty cells are
dropped; no table on the page returns `{"headers": [], "rows": []}` and never
raises.

### agent/url_banner.py
`stamp_url(png_bytes, url)` composites a 32px harness-drawn strip showing the
page URL onto the top of a screenshot (Chrome has no real address bar to
capture in a headless/automated viewport). Pure and browser-free — unit-tests
without Playwright — and never raises: any failure (Pillow missing, undecodable
image) returns the original bytes unchanged, so a cosmetic banner can never
fail a step. Requires `Pillow` (see `requirements.txt`). The evaluator prompt
(`prompts/result_evaluator.txt`) is told about the strip so it isn't mistaken
for application content.

### agent/orchestrator.py
The main loop. Per test case: fetch detail → translate each step → open browser →
execute + screenshot each step → evaluate → resolve step in run_state → post to
QMetry → (if fail and bugs enabled) create Jira bug → close browser. Updates
run_state after every step so the frontend stays live. Catches all per-case
exceptions so one bad case never kills the run.

**PAGE DATA block (2026-08-13).** TC-2915 ("Verify Cannot Edit Email Address to
One That Already Exists") has empty QMetry `test_data`, so the model invented
`existing.user@example.com` — an address that exists nowhere — and the app
accepted the "duplicate" edit: the negative test verified nothing. `_execute_step`
/ `_attempt_step` now detect, via `_step_needs_existing_data` matching the
step's action + expected text (case-insensitive) against `_EXISTING_DATA_PHRASES`
(`already exists`, `already assigned`, `already in use`, `already taken`,
`already registered`, `duplicate`, `another user`, `existing user`, `an
existing`, `that already`), whether a step needs a value that must already
exist in the app. When it matches AND the run is live (not dry-run),
`_build_page_data_block` calls `BrowserSession.snapshot_table_data()` once per
attempt and appends a `PAGE DATA` block (labelled as real on-screen values) to
the translator context; `prompts/step_translator.txt` tells the model to take
such a value verbatim from PAGE DATA (never a placeholder), pick a row
different from the record being edited, and say so via no actions rather than
fabricate if nothing suitable is present. For every non-matching step the
context is unchanged and `snapshot_table_data` is never called. PAGE DATA can
carry real user emails — that's fine in a model prompt, but it must never
reach a log line, `run_state`, or an SSE event; only its row count is logged.

**Carry-forward table memory (2026-08-13 fix).** The live run of TC-2915 above
still failed: the 12 real emails are on the Users list at step 0, but by step
2 (the triggering step) the case is on the Edit User page, which has no
table — reading only the *current* page can't work for any test that
navigates away from the list before it needs the value. `_execute_case`
creates one `_TableMemory()` per case (a plain local variable threaded through
`_execute_step`/`_attempt_step` as an argument — **never stored on `self`** —
so it is scoped to exactly one case and can never leak into the next case's
context) and passes it down. After any action that navigates, `_attempt_step`
opportunistically calls `_capture_table_opportunistically` — cheap, no model
tokens — and remembers the page's table if non-empty, replacing whatever was
remembered before; this is what lets the Users-list step (which never itself
triggers PAGE DATA) leave its table available for step 2. `_build_page_data_block`
still prefers the current page's table when non-empty (and refreshes the
memory from it too); only when the current page has nothing does it fall back
to the remembered table, and it labels that block as "seen earlier in this
case on `<url>`" so the model knows the values are real but not currently on
screen. `prompts/step_translator.txt`'s PAGE DATA rule got one additive
sentence permitting values seen earlier in the case — nothing else in that
prompt changed (a broader, unvalidated edit already cost this project one
disqualified evaluator-prompt variant; see the notice atop
`prompts/result_evaluator_41.txt`).

**Step-attempt retries never repeat a clean run for a bad verdict (2026-08-13
fix).** The retry/escalation loop (`self.step_attempts`) exists for "the agent
could not perform the action" — a `BrowserError` or a translate failure. A
live run showed it also firing when every action succeeded and only the
*evaluator's verdict* was non-pass (e.g. Save clicked fine, but the saved
value didn't match): retrying can't change a verdict about the app's
behaviour, and for a committing action (Save/Delete/Submit) it only re-mutates
the system under test. `_attempt_step` now returns a 4th value, `execution_ok`
— True only when every action performed this attempt succeeded and evaluation
was reached with no `BrowserError` and no translate failure along the way
(the "judge on partial evidence after persistent errors" paths are NOT
`execution_ok`, since those did hit a real problem and still deserve a retry).
`_execute_step` skips remaining attempts when `status != "pass" and
execution_ok and not is_last`, logging at INFO with the step and verdict. The
"NEEDS HUMAN REVIEW (N agent attempts)" suffix now reports attempts actually
used (`attempt`, not `attempts_max`), so a skip correctly reads "(1 agent
attempts)". `STEP_MAX_ATTEMPTS`'s default and meaning for genuine action
failures are unchanged.

### agent/reporter.py
After a run, generates `reports/run_<timestamp>_<run_id>.html`: self-contained HTML
(inline CSS, Duke navy palette) with totals + per-case table showing status,
detail, evaluation reason, and per-step duration. Each step's `screenshot_b64`
(when present) is embedded as a data-URL `<img>` inside a collapsed `<details>`
disclosure on that row — closed by default so a many-step case doesn't ship a
multi-megabyte page open to a wall of images; steps with no screenshot render
no markup at all. Served over HTTP at `/reports/<filename>` (server.py mounts
`reports/` as static files) so the console's "View report" button can open it
in a new tab — `file://` navigation from an `http://` page is blocked by Chrome.

### server.py
FastAPI app. Endpoints (exactly what the frontend calls — see FRONTEND.md):
- `POST /runs` `{ "plan": "SOUSCLOUD-TP-45" }` → starts a run in a background task,
  returns `{ "run_id": ... }`.
- `GET /runs/{id}` → current run_state JSON.
- `GET /runs/{id}/stream` → SSE stream of step/status events (Mode B).
- `POST /runs/{id}/report` → generate HTML report, return its path/url.
- `POST /runs/{id}/log-bugs` → create Jira bugs for failed cases. Gated action —
  only succeeds on a finished run that has failures.
- `GET /cycles?q=&start=&limit=` → one page of test runs `{id, key, name}`.
- `GET /testcases?q=&start=&limit=` → one page of the project's test case library
  `{id, key, name, plan_key}`. Both push `q` down to QMetry and return
  `total` + `next_start`.
- `GET /manual/{plan}` → manual session state (`{plan}` = cycle id/key, or
  `TC:<case key>` for a library case).
- `GET /manual/{plan}/cases/{id}/steps` → hydrate one case's steps on demand.
- `POST /manual/{plan}/cases/{id}/mark` → record a hand mark.
- `POST /manual/{plan}/cases/{id}/run-agent` → run one case with the agent.
- `POST /manual/{plan}/push-qmetry` → gated push of manual results to QMetry.
- Serves `frontend/dist` as static files in production.
CORS: allow only `FRONTEND_ORIGIN`.

---

## Frontend build rules

**Read FRONTEND.md first and follow it exactly.** It specifies the Duke navy token
system, the DM Mono / Inter type split, the two-panel layout, every component, the
copy voice, and the run_state contract. Key non-negotiables:

- Duke navy (`#1B2A6B`) is the brand color. Rail is navy; primary buttons are navy.
- The **execution tape is the signature** — steps stream in with a spinner, resolve
  to pass/fail with the AI evaluation beneath. Do not replace it with a static table
  or a grid of cards.
- DM Mono for machine output (IDs, selectors, timings), Inter for human-readable text.
- The "Log failures to Jira" button is **gated**: disabled during a run and when
  there are zero failures. The frontend must never let it fire otherwise — the
  backend also enforces this, but the UI gate is part of the design.
- The frontend talks ONLY to `server.py`. It never holds credentials and never calls
  QMetry / Jira / Azure directly.
- No CSS framework, no browser storage, responsive to mobile, keyboard focus visible,
  `prefers-reduced-motion` respected.

Use the Duke logo asset at `frontend/public/duke-logo.png`. If it's not present yet,
use the white-shield-with-"Duke"-wordmark placeholder described in FRONTEND.md.

---

## Error handling rules (backend)

- All API calls wrapped in try/except with retry (max 3, exponential backoff).
- Test case with no steps → BLOCKED, reason "No steps defined".
- Azure AI returns invalid JSON → retry once → else BLOCKED.
- Playwright throws on an action → capture exception as failure reason, screenshot,
  mark step FAIL.
- App shows login page mid-test → BLOCKED (session expired).
- One case crashing must never kill the run — catch per case, record BLOCKED, continue.
- Every error logged before continuing (use `logging`, not print).

---

## Code style

- Backend: Python 3.11+, async throughout, `httpx` for async HTTP, type hints,
  docstrings, config from env via `python-dotenv`, `logging` not print.
- Frontend: see FRONTEND.md. React functional components + hooks, or plain HTML/JS.
  Hand-written CSS from the token system. No framework defaults.

---

## Things NOT to do

- Do not put any credential in the frontend or in any browser-visible code.
- Do not let the frontend call QMetry / Jira / Azure directly — always via server.py.
- Do not hardcode plan IDs, project keys, or URLs — env or request args only.
- Do not commit `.env`.
- Do not redesign the UI away from FRONTEND.md. If a real constraint forces a change,
  update FRONTEND.md in the same change and say what changed and why.
- Do not change the run_state shape without updating FRONTEND.md and the frontend hook.

---

## Current state of play

The agent loop, server, reporter, and Jira client are implemented and unit-tested
against mocks. `pytest tests/ -q` passes (73 tests). The frontend is wired and
renders against `fixtures/sample_run_state.json` until a real run id exists.

**Already done:** scaffold; `agent/run_state.py`; `agent/azure_ai.py`; `agent/browser.py`;
`agent/orchestrator.py`; `agent/case_source.py` with `FixtureCaseSource`;
`agent/reporter.py`; `agent/jira_client.py`; `server.py` (all five endpoints
including SSE); frontend; the test suite.

**Open work, in order:**

1. ~~Drop missing values into `.env`~~ **DONE (2026-07-02).** All values filled and
   verified live.
2. ~~Decide and build the Sous Chef Cloud login flow~~ **DONE.** `agent/login.py`
   form-fills email/password (no SSO), waits on `wait_until="commit"` +
   element selectors (legacy pages stall DOMContentLoaded), and dismisses the
   cookie-consent banner both before and after login.
3. ~~Capture the real QMetry endpoint shape~~ **DONE (2026-06-30).** `agent/qmetry.py`
   + `QMetryCaseSource` work against the live API; `server.py` auto-selects it when
   `QMETRY_API_KEY` is set. The **Manual + Agent test view** (new) consumes it — see
   `agent/manual_state.py` and the `/manual/*` endpoints. Open the Manual tab at
   `http://localhost:5173/?cycle=<idOrKey>` (e.g. `?cycle=1ZwYH2ObF7AGZa`).
4. **Rotate the QMetry API key** — keys have been pasted into chat transcripts
   (2026-06-17 and 2026-06-30). The one currently in `.env` works but is exposed.
5. **CLI end-to-end: DONE (2026-07-02).** `main.py --plan SOUSCLOUD-TP-45` passes
   3/3 live against test.souscheftech.com (~82s) and writes the HTML report.
   Three fixes made it pass: `BrowserSession.wait_for_settle()` before every
   screenshot (networkidle ≤15s + 800ms — animations and slow server-side
   navigations otherwise get screenshotted mid-flight); a translator-prompt rule
   that PAGE ELEMENTS are already visible so never click a parent menu to reveal
   a listed element (the Recipe toggle was collapsing the open submenu); and the
   post-login cookie-banner dismissal. **Frontend e2e also verified same day**
   (Live run + Manual tab against cycle `1ZwYH2ObF7AGZa`).
6. **Only after the above works:** flip `AUTO_CREATE_BUGS=true` for a full plan
   run.

**2026-07-02 (evening) additions — all live and tested (135 tests):**
- Both AI roles moved to the `gpt-5.4-mini` deployment
  (`AZURE_AI_TRANSLATOR_DEPLOYMENT` / `AZURE_AI_EVALUATOR_DEPLOYMENT`, fall back
  to `AZURE_AI_DEPLOYMENT`; client auto-drops `temperature` for reasoning models).
- **Step-selection agent runs** (Manual tab): per-step "agent" checkboxes,
  optional `{"steps": [...]}` body on run-agent, `agent_steps` on the manual
  mark, hint chips. Spec/plan under `docs/superpowers/`.
- **`login` browser action**: the model requests it, the harness executes it
  with `.env` credentials — the model never sees credentials.
- **QMetry step-text cleaning** (`clean_step_text` in `agent/qmetry.py`) +
  `testData` included in step actions.
- `QMETRY_DEFAULT_CYCLE` + `GET /config`: the console opens on the real cycle.
- Server watchdog: `scripts/serve.cmd` (start manually after reboot, or register
  the "SousChef QA Console" scheduled task).
- ~~**Known blocker:** TC-1985/1987/2211 need their `[~id]` menu references
  rewritten as plain text IN QMETRY~~ **FIXED 2026-08-04 in code, no QMetry edits
  needed** — see the parameter-resolution note below.
- **Known blocker:** TC-2 step 3 (accordion sidebar) is un-verifiable with one
  screenshot — proposed per-action screenshots feature is designed but awaiting
  approval.

**2026-07-07/08 additions — all live, 185 tests:**
- `logout` browser action + RECONCILE-FIRST translator rule; the translator
  receives the step's EXPECTED RESULT; the evaluator receives PERFORMED
  ACTIONS + STEP INSTRUCTION and must fill a `waived` JSON field for
  conditional "If available…" clauses (absent feature = waived = pass, never
  fail/blocked). Role mentions default to the signed-in account; the harness
  browser is declared as Chromium.
- Cases continue past failed/blocked steps (outcome: fail > blocked > pass);
  runs are cancellable (`POST /runs/{id}/cancel` + UI Cancel button).
- Manual tab: precondition shown per case (QMetry only returns it with
  `?fields=summary,precondition`); per-case login credentials (password
  persisted plaintext in `manual_sessions/`, NEVER in HTTP payloads; empty =
  .env admin); agent runs auto-write `agent_note` onto the mark (pushed in
  the QMetry comment); clean start page (nothing loads until a TR is chosen).
- **Evaluator-prompt iteration harness:** `scripts/prompt_eval/` — capture a
  real step's evaluator inputs once, then judge them N times per prompt edit
  (validate both directions). Lesson: for gpt-4o compliance, output-schema
  slots beat rule bullets; never judge a prompt edit on a single live run.

**2026-08-04 — TR/TC rail browser + deferred steps (255 tests):**
- The rail replaces the cycle `<select>` with a two-level browser: a `TR`|`TC`
  toggle, server-side debounced search over **all** 410 runs / 2534 cases, 50 per
  page with `Load more`. Picking a TR drills in; picking a TC opens that one case
  and keeps the list up. Spec: `docs/superpowers/specs/2026-08-04-tr-tc-browser-
  and-lazy-loading-design.md`.
- Runs now open in ~2s instead of ~21s for a 73-case cycle (10x): names and
  preconditions ride along on the case search, and steps load per opened case.
- Deep links: `?cycle=<idOrKey>` for a run, `?tc=<case key>` for a library case.
- Follow-ups the same day: the rail picker is a single `<select>` (runs and cases
  are alternatives, not tabs to compare); search accepts **keys** (`2075`,
  `TC-2075`) and is **word-order independent** — `"recipe delete"` used to return
  0 while `"delete recipe"` returned 6, both now return the same 31; rail rows
  show the **full** `SOUSCLOUD-TC-####` key; and `duke-logo.png` was replaced (the
  shipped file had an opaque black background, so the white brand tile rendered
  as a black box).
- **Per-step test data + agent-only verdicts (273 tests).** `test_data` is its own
  field on each step (it used to be glued onto the action text); the console shows
  it per step with an italic "none" when absent. QMetry has a case-level
  `testData` field but it is **always null** — test data is per step only.
  `Orchestrator._execute_step` re-joins action + test data for the model, so the
  prompt is unchanged. All per-step Pass/Fail/Blocked/Skip buttons are gone: the
  agent's verdict is the indication, so `ManualStore.set_agent` writes the run's
  outcome onto the case status (a pre-existing hand step-mark still wins), which
  is also what keeps the QMetry push gate reachable.
- **`[~id]` tokens are PARAMETERS, not user mentions (fixed 2026-08-04).**
  `GET /testcases/{idOrKey}/versions/{no}/parameters` resolves them:
  `[{"rowIndex": 1, "params": [{"parameterId": 20322, "parameterName": "User
  Role", "value": "Admin"}]}]`. `clean_step_text(text, params)` substitutes them
  and `_load_steps` fetches them per case, so shareable steps read correctly per
  case (`[~20322]` is "Admin" in TC-2 but "Access Manager" in TC-2579). Extra
  `rowIndex` rows are data-driven iterations — only the first is used, and a case
  with more is logged. Left unresolved these caused real damage: TC-1985 could
  not identify its menu, went to Create Site instead of Edit Inventory, and
  failed or blocked 14 of 26 steps. Preconditions never contain tokens (checked
  across 500 cases), so resolution lives only in the per-case steps path and
  costs nothing in the cheap case list.
- **Case-level test data.** The same parameter rows are exposed per case as
  `test_data: [{name, value}]` (QMetry calls the parameter table "Test Data"),
  rendered under the precondition — e.g. TC-1985 shows `User Role=Admin,
  PHU Type=RFHU (H2), Menu=Recipe, SubMenu=Edit Inventory`. It costs no extra
  call: it comes from the same request that resolves the step tokens. Cached in
  `_CASE_TEST_DATA_CACHE` alongside steps so a list refresh doesn't drop it.
  The per-step `test_data` string is separate and unrelated (QMetry's per-step
  testData field).
- **Agent-notes panel removed from the UI.** It restated every step verdict as a
  wall of text under the steps that already showed them. `agent_note` is still
  recorded and still goes into the QMetry comment on push.
- **Stranded "running" cases self-heal.** `agent_status` is persisted but run
  state is in memory, so a kill/restart left a case "running" forever with Run
  and Push both disabled. `GET /manual/{plan}` now clears any "running" mark
  whose run id this process doesn't own, appending "interrupted (server
  restarted)" to the agent note.

**Writing test steps that can pass:** step text must reference the app's real UI.
Left nav: Dashboard, Equipment, Recipe → (Edit Inventory `/account/recipes`,
HS2 Configurator, IRHS-E Configurator, RFHU Configurator), Account, Sites, Users,
Account Requests, Help, Logs. `fixtures/sample_plan.json` matches the real nav.

---

## Reference links

- QMetry for Jira Cloud REST API: https://documentation.qmetry.com/qtm4j/rest-api/
- Jira REST API v3: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- Azure OpenAI GPT-4o: https://learn.microsoft.com/en-us/azure/ai-services/openai/
- Playwright Python: https://playwright.dev/python/docs/intro
- Azure AI Foundry: https://learn.microsoft.com/en-us/azure/ai-foundry/
- FastAPI: https://fastapi.tiangolo.com/
- Vite: https://vitejs.dev/
