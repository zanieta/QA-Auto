# Role Defaulting + Agent Notes + Continue-Past-Failures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three agent-run improvements (spec: `docs/superpowers/specs/2026-07-07-role-default-agent-notes-continue-design.md`): treat the signed-in account as any referenced role; auto-record agent findings as a per-case note; run every selected step even after a fail/blocked step.

**Architecture:** (1) prompt-only rules in both prompt files; (2) `ManualMark.agent_note` field + pure `compose_agent_note()` helper in `agent/manual_state.py`, wired in `server.py._run_agent_case`, surfaced in `ManualCase.jsx` + FRONTEND.md; (3) remove the two `break`s in `orchestrator._execute_case` and compute case outcome by precedence fail > blocked > pass.

**Tech Stack:** Python 3.14 (`.venv\Scripts\python.exe`), pytest + AsyncMock (no network/Chromium), React + Vite frontend.

## Global Constraints

- Always run Python via `.venv\Scripts\python.exe` from C:\Users\rsantos\AI\QA.
- NOT a git repository — no git commands; a task's "commit" is its green test run.
- Tests never hit the network or launch Chromium.
- run_state shape is UNCHANGED. The manual-session shape changes (new `agent_note`) — FRONTEND.md and the frontend must be updated in the same task (Task 5).
- The model never sees credentials; `login`/`logout` stay harness-executed.
- `RUN_MODE` (cross-CASE behavior) is untouched.

---

### Task 1: `agent_note` on the mark + `compose_agent_note` helper

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\agent\manual_state.py` (ManualMark ~line 33-65, compose_comment ~line 128, ManualStore.set_agent ~line 201)
- Test: `C:\Users\rsantos\AI\QA\tests\test_manual_state.py` (append; file has a `store` fixture whose session contains case "IRHS-R-01" in plan "TP-45" — reuse it, see existing `test_set_agent_and_mark_pushed` ~line 85)

**Interfaces:**
- Consumes: `agent.run_state` `TestCase` (fields: `id`, `name`, `status`, `steps`) and `Step` (fields: `action`, `detail`, `status`, `evaluation`, `duration_seconds`).
- Produces (Task 2 and Task 5 rely on these exactly):
  - `ManualMark.agent_note: str = ""` — serialized as `"agent_note"` in `to_dict()`, default `""` when absent in `from_dict()`.
  - `ManualStore.set_agent(plan_key, case_id, agent_status, agent_run_id, agent_steps=_UNSET, agent_note=_UNSET)` — `_UNSET` leaves the existing note.
  - `compose_agent_note(run_case, run_id: str, step_indices: list[int] | None, when: str) -> str` (module-level in manual_state.py).
  - `compose_comment(case)` appends the agent note last, separated by a blank line.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_manual_state.py`:

```python
def test_agent_note_serializes_and_defaults_empty(store):
    store.set_agent("TP-45", "IRHS-R-01", "pass", "run-1", agent_note="Step 1: pass — ok")
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.to_dict()["agent_note"] == "Step 1: pass — ok"
    # old snapshots without the key load as ""
    from agent.manual_state import ManualMark
    assert ManualMark.from_dict({"status": "pass"}).agent_note == ""


def test_set_agent_without_note_preserves_note(store):
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-1", agent_note="kept")
    store.set_agent("TP-45", "IRHS-R-01", "pass", "run-1")
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.agent_note == "kept"


def test_compose_comment_appends_agent_note(store):
    case = store.set_mark("TP-45", "IRHS-R-01", "fail", "flaky toggle", [0])
    store.set_agent("TP-45", "IRHS-R-01", "fail", "run-1", agent_note="Agent run x")
    text = compose_comment(case)
    assert text.startswith("flaky toggle")
    assert "Fail at: step 1" in text
    assert text.endswith("\n\nAgent run x")


def test_compose_agent_note_formats_header_and_steps():
    from agent.manual_state import compose_agent_note
    from agent.run_state import Step, TestCase

    case = TestCase(id="TC-2", name="Login", status="fail")
    case.steps = [
        Step(action="a", detail="d", status="pass", evaluation="Login page visible."),
        Step(action="b", detail="d", status="fail", evaluation="Navigation not demonstrated."),
    ]
    note = compose_agent_note(case, "run-9", [0, 3], "2026-07-07 14:32")
    lines = note.splitlines()
    assert lines[0] == "Agent run 2026-07-07 14:32 (run-9), steps 1, 4: fail"
    assert lines[1] == "Step 1: pass — Login page visible."
    assert lines[2] == "Step 4: fail — Navigation not demonstrated."
```

(If `TestCase`/`Step` constructor signatures differ — read `agent/run_state.py`
first and build them the way `run_state`'s own tests do; the assertions stay.)

- [ ] **Step 2: Run to verify failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py -q -k "agent_note or compose_agent"`
Expected: FAIL (unexpected keyword `agent_note`; no `compose_agent_note`)

- [ ] **Step 3: Implement.** In `agent/manual_state.py`:

`ManualMark`: add field + serialization:

```python
    agent_note: str = ""          # latest agent-run summary; tester comment stays separate
```

In `to_dict()` add `"agent_note": self.agent_note,` and in `from_dict()` add
`agent_note=d.get("agent_note", ""),`.

`ManualStore.set_agent`: add parameter `agent_note: Any = _UNSET` and before
`self._persist(...)`:

```python
        if agent_note is not _UNSET:
            case.mark.agent_note = agent_note or ""
```

`compose_comment`: append after the failed-steps loop, before the join:

```python
    if mark.agent_note:
        if lines:
            lines.append("")
        lines.append(mark.agent_note)
```

(The blank entry produces the `\n\n` separator via `"\n".join`.)

Module-level helper (place next to `compose_comment`):

```python
def compose_agent_note(run_case, run_id: str, step_indices: list[int] | None, when: str) -> str:
    """One-paragraph summary of an agent run, written onto the case's mark.

    Step numbers are the ORIGINAL case step numbers (selected indices + 1),
    not tape positions — the tape only contains the selected steps, in order.
    """
    sel = sorted(set(step_indices)) if step_indices else list(range(len(run_case.steps)))
    nums = ", ".join(str(i + 1) for i in sel[: len(run_case.steps)])
    lines = [f"Agent run {when} ({run_id}), steps {nums}: {run_case.status}"]
    for orig, st in zip(sel, run_case.steps):
        evaluation = " ".join((st.evaluation or "").split())
        lines.append(f"Step {orig + 1}: {st.status} — {evaluation}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py -q`
Expected: ALL pass.

---

### Task 2: server writes the note on run completion

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\server.py` — `_run_agent_case` (~lines 179-203)
- Test: `C:\Users\rsantos\AI\QA\tests\test_server.py` (append near the run-agent tests, see `test_run_agent_starts_single_case` ~line 234 for the fixtures/monkeypatching pattern)

**Interfaces:**
- Consumes (from Task 1): `compose_agent_note(run_case, run_id, step_indices, when)`; `MANUAL.set_agent(..., agent_note=...)`.
- Produces: after any manual agent run, `mark.agent_note` is set.

- [ ] **Step 1: Write the failing test.** Read the existing run-agent tests in
`tests/test_server.py` first and reuse their `client`/`tmp_path`/`monkeypatch`
setup. The test drives `server_mod._run_agent_case` directly with a stub
orchestrator so the real one never runs:

```python
def test_run_agent_completion_writes_agent_note(client, tmp_path, monkeypatch):
    # build the session like the other run-agent tests do (GET /manual/TP-45
    # with the fixture case source), then:
    import asyncio
    from agent.run_state import new_run_state, TestCase

    server_mod = _server()  # or however this file imports the server module

    final = new_run_state("TP-45", "TP-45")
    final.add_case(TestCase(id="A", name="Case A"))
    final.start_case("A")
    final.add_step("A", "do the thing")
    final.resolve_step("A", 0, "pass", "Looks right.", 1.0)
    final.resolve_case("A", "pass")

    class FakeOrch:
        async def run_single_case(self, case_id, plan_key=None, step_indices=None):
            return final

    monkeypatch.setattr(server_mod, "_build_orchestrator", lambda cb: FakeOrch())
    state = new_run_state("TP-45", "TP-45")
    asyncio.get_event_loop().run_until_complete(
        server_mod._run_agent_case(state.run_id, "TP-45", "A", state, [2])
    )
    mark = server_mod.MANUAL.get("TP-45").find_case("A").mark
    assert mark.agent_note.splitlines()[0].startswith("Agent run ")
    assert state.run_id in mark.agent_note  # header carries the run id
    assert "steps 3: pass" in mark.agent_note.splitlines()[0]
    assert "Step 3: pass — Looks right." in mark.agent_note
```

Adapt mechanics (module import name, event-loop invocation, add_step/resolve
signatures — read `agent/run_state.py`) to the file's conventions; keep the
assertions on the note content exactly.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -q -k agent_note
Expected: FAIL (`agent_note` stays "")

- [ ] **Step 3: Implement.** In `server.py` `_run_agent_case`, replace the two
`MANUAL.set_agent(...)` completion calls:

```python
    try:
        orch = _build_orchestrator(_make_on_update(run_id))
        final = await orch.run_single_case(
            case_id, plan_key=plan, step_indices=step_indices
        )
        RUNS[run_id] = final
        case = next((c for c in final.test_cases if c.id == case_id), None)
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        note = (
            compose_agent_note(case, run_id, step_indices, when)
            if case is not None
            else f"Agent run {when} ({run_id}): case missing from run state"
        )
        MANUAL.set_agent(
            plan, case_id, case.status if case else "blocked", run_id, agent_note=note
        )
    except Exception:
        log.exception("Manual agent run %s crashed", run_id)
        state.finish()
        _make_on_update(run_id)(state)
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        MANUAL.set_agent(
            plan, case_id, "blocked", run_id,
            agent_note=f"Agent run {when} ({run_id}): crashed — see server log",
        )
```

Add imports if missing: `from datetime import datetime` and extend the
existing `from agent.manual_state import ...` line with `compose_agent_note`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -q`
Expected: ALL pass.

---

### Task 3: continue past failed/blocked steps

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\agent\orchestrator.py` — `_execute_case` loop (~lines 186-201)
- Test: `C:\Users\rsantos\AI\QA\tests\test_orchestrator.py` (read the file's existing fakes for azure/browser and its run helpers first; there are existing multi-step tests to model on)

**Interfaces:**
- Consumes: `_execute_step` returning `'pass' | 'fail' | 'blocked'` (unchanged).
- Produces: every selected step executes; case outcome = `fail` if any step failed, else `blocked` if any blocked, else `pass`.

- [ ] **Step 1: Write the failing tests.** Two tests in
`tests/test_orchestrator.py`, using the file's existing fake-translator/fake-
browser fixtures (adapt mechanics, keep assertions):

```python
@pytest.mark.asyncio
async def test_failed_step_does_not_stop_remaining_steps(...):
    # 3-step case; make step 1's evaluation "fail", steps 2-3 "pass"
    state = await orch.run_plan(...)
    case = state.test_cases[0]
    assert [s.status for s in case.steps] == ["fail", "pass", "pass"]
    assert case.status == "fail"


@pytest.mark.asyncio
async def test_blocked_step_does_not_stop_remaining_steps(...):
    # 3-step case; step 2 evaluates "blocked", steps 1 and 3 "pass"
    state = await orch.run_plan(...)
    case = state.test_cases[0]
    assert [s.status for s in case.steps] == ["pass", "blocked", "pass"]
    assert case.status == "blocked"
```

- [ ] **Step 2: Run to verify they fail** (today the loop breaks: later steps
never appear / stay unresolved)

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -q -k "does_not_stop"`
Expected: 2 FAILED.

- [ ] **Step 3: Implement.** In `_execute_case`, replace:

```python
        outcome: str = "pass"
        try:
            # tape_index (position in the executed sequence) is what resolve_step
            # indexes — NOT the original step number, which differs when filtering.
            for tape_index, (orig_index, step) in enumerate(selected):
                step_outcome = await self._execute_step(
                    state, case_id, tape_index, step, browser, dry_run=dry_run,
                    case_context=_case_brief(orig_index),
                )
                step_status[orig_index] = step_outcome
                if step_outcome == "fail":
                    outcome = "fail"
                    break
                if step_outcome == "blocked":
                    outcome = "blocked"
                    break
```

with:

```python
        outcome: str = "pass"
        try:
            # tape_index (position in the executed sequence) is what resolve_step
            # indexes — NOT the original step number, which differs when filtering.
            # A fail/blocked step records and the case CONTINUES: each step
            # re-plans from the live page (RECONCILE), and a step whose
            # prerequisite truly broke fails/blocks on its own evidence.
            for tape_index, (orig_index, step) in enumerate(selected):
                step_outcome = await self._execute_step(
                    state, case_id, tape_index, step, browser, dry_run=dry_run,
                    case_context=_case_brief(orig_index),
                )
                step_status[orig_index] = step_outcome
                if step_outcome == "fail":
                    outcome = "fail"
                elif step_outcome == "blocked" and outcome != "fail":
                    outcome = "blocked"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -q`
Expected: ALL pass. If an existing test asserts the OLD stop-on-fail behavior
(e.g. "later steps skipped after fail"), that test now encodes outdated spec:
update it to assert the new continue behavior and say so in your report.

---

### Task 4: role-defaulting prompt rules

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\prompts\result_evaluator.txt` and `C:\Users\rsantos\AI\QA\prompts\step_translator.txt`

- [ ] **Step 1:** In `result_evaluator.txt`, under `Reasoning rules:`, insert
this bullet immediately AFTER the "FIRST question…" blocked bullet (so setup
visibility still wins) and BEFORE the '"pass" means…' bullet:

```
- ROLE DEFAULTING: the test account signed in by the harness IS whatever role
  the expectation references ("Admin", "Recipe Admin", or an
  "[unresolved reference]" role token) unless the step explicitly requires a
  DIFFERENT named account. Never fail or block a step solely because the
  frames cannot prove the account's identity, role name, or permission set —
  judge the visible layout and menus against what the expectation lists.
```

- [ ] **Step 2:** In `step_translator.txt`, append this bullet at the end of
the `Rules:` list:

```
- A role mentioned in the step ("Admin", "Recipe Admin", "[unresolved
  reference]") is NOT an instruction to find other credentials: the harness
  {"action": "login"} account already has that role unless the step itself
  supplies different credentials (which you still never type — login only).
```

- [ ] **Step 3: Sanity-check**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: ALL pass (prompts are data).

---

### Task 5: frontend agent-note display + FRONTEND.md contract

**Files:**
- Modify: `C:\Users\rsantos\AI\QA\frontend\src\components\ManualCase.jsx` (notes block ~line 191), `C:\Users\rsantos\AI\QA\frontend\src\tokens.css`, `C:\Users\rsantos\AI\QA\FRONTEND.md` (manual session JSON ~line 261-269)
- Verify: `npm run build` in `C:\Users\rsantos\AI\QA\frontend`

**Interfaces:**
- Consumes: `manual.agent_note` (string, may be "") from Task 1's serialization.

- [ ] **Step 1:** In `ManualCase.jsx`, the component reads the mark as `m` and
renders a `.manual-notes` div (label "Notes" + textarea). Insert immediately
AFTER that `.manual-notes` div:

```jsx
        {m.agent_note ? (
          <div className="agent-note" aria-label="Agent run notes">
            <div className="agent-note-label">Agent notes</div>
            <pre>{m.agent_note}</pre>
          </div>
        ) : null}
```

(If the mark variable is named differently in scope there, match it — the
existing `comment` state initializer shows which variable holds the mark.)

- [ ] **Step 2:** In `tokens.css`, add beside the existing manual-view styles:

```css
.agent-note {
  margin-top: 8px;
  padding: 10px 12px;
  background: var(--surface-sunken, rgba(27, 42, 107, 0.04));
  border-radius: 6px;
}

.agent-note-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-muted, #6b7280);
  margin-bottom: 4px;
}

.agent-note pre {
  margin: 0;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: inherit;
}
```

(Check tokens.css's actual custom-property names near the top of the file and
use the file's existing muted-text/sunken-surface tokens instead of the
fallbacks if they exist.)

- [ ] **Step 3:** In `FRONTEND.md`'s manual-session JSON block, add after the
`"agent_steps"` line:

```
        "agent_note": "",             // latest agent-run summary (per-step verdicts + findings)
```

- [ ] **Step 4: Build**

Run (in `C:\Users\rsantos\AI\QA\frontend`): `npm run build`
Expected: build succeeds, `dist/` updated.

- [ ] **Step 5: Suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: ALL pass — note `test_fixture_matches_built_session_shape` and
`test_frontend_fixture_copy_is_identical` (tests/test_manual_state.py:143,164)
compare the serialized shape against `fixtures/sample_run_state.json` /
frontend fixture copies. If they fail on the new `agent_note` key, update
`fixtures/` and `frontend/public/fixtures/` manual-session fixture files to
include `"agent_note": ""` so fixture parity holds (that is part of this
task).

---

### Task 6: verification (controller-run)

- Full suite green.
- Restart the server (kill :8000, `scripts\serve.cmd`), rehydrate
  `GET /manual/daYoCqgmH49VMx`.
- Live acceptance per spec: agent steps 1,3,4,5 on TC-2 — step 4 fail no
  longer stops step 5; the mark's `agent_note` contains one line per run step;
  step 2 (run separately) no longer blocks on the role reference.
