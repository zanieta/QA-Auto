# Step-Selection Agent Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the Manual tab, each test-case step gets a checkbox; the agent executes only the checked steps, shows per-step results as informational hints, and the tester keeps the Pass/Fail/Blocked verdict.

**Architecture:** The orchestrator's `run_single_case` gains a `step_indices` filter (run-state tape contains only executed steps — contract unchanged). The `run-agent` endpoint accepts an optional `{"steps": [...]}` body and records the selection as `agent_steps` on the manual mark. The React `ManualCase` component adds per-step "agent" checkboxes, a "Run selected steps with agent" button, and an `agent: pass/fail` chip per executed step.

**Tech Stack:** Python 3.14 / FastAPI / pytest (mocked httpx + Playwright), React + Vite, hand-written CSS from `tokens.css`.

**Spec:** `docs/superpowers/specs/2026-07-02-step-selection-agent-runs-design.md`

## Global Constraints

- This repo is **NOT a git repository** — there are no commit steps. After each task, run the full suite instead: `.venv\Scripts\python.exe -m pytest tests/ -q` (must stay green; 120 tests before this plan).
- Always invoke Python as `.venv\Scripts\python.exe` (Windows venv at repo root).
- Frontend copy rules (FRONTEND.md): sentence case, buttons say what happens, DM Mono for machine output, Inter for human text, no CSS framework, `prefers-reduced-motion` respected, keyboard focus visible.
- The AI verdict is a **hint only** — nothing may auto-set the manual `status`; only the tester's mark buttons do.
- run_state JSON shape must NOT change (no new step status values).
- All colors via the CSS custom properties in `tokens.css` — never hardcode hex inline.

---

### Task 1: Orchestrator `step_indices` filter

**Files:**
- Modify: `agent/orchestrator.py` (`run_single_case` ~line 93, `_execute_case` ~line 115)
- Test: `tests/test_orchestrator.py` (append at end)

**Interfaces:**
- Produces: `Orchestrator.run_single_case(case_id: str, plan_key: str = "", dry_run: bool = False, step_indices: list[int] | None = None) -> RunState`. `step_indices` are 0-based original step positions; `None` = all steps. Out-of-range indices are ignored; duplicates deduped; execution order ascending. If nothing remains after filtering, the case resolves `blocked` (same as a case with no steps).
- Consumes: existing test helpers in `tests/test_orchestrator.py`: `FakeCaseSource(plan_meta, cases)`, `_fake_azure(translate_side_effect, evaluate_side_effect)`, `_fake_browser()`, `_ok_actions()`, and the autouse `mock_login` fixture (login is already patched — do not patch it again).

**Important subtlety:** `RunState.resolve_step(case_id, step_index, ...)` indexes into the run-state case's `steps` **list position**, not the original step number. When filtering, the loop must pass the *tape position* (0..n-1 of executed steps) to `_execute_step`, not the original index — otherwise `case.steps[step_index]` raises IndexError.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_run_single_case_step_indices_runs_only_selected_steps():
    cases = [
        {"id": "A", "name": "Alpha", "steps": [
            {"action": "Step one", "expected": "E1"},
            {"action": "Step two", "expected": "E2"},
            {"action": "Step three", "expected": "E3"},
        ]},
    ]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions(), _ok_actions()],
        evaluate_side_effect=[
            {"status": "pass", "reason": "ok"},
            {"status": "pass", "reason": "ok"},
        ],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A", step_indices=[2, 0, 2])  # unsorted + dup
    case = state.test_cases[0]
    # tape contains ONLY the executed steps, ascending original order
    assert [s.action for s in case.steps] == ["Step one", "Step three"]
    assert case.status == "pass"
    translated = [c.args[0] for c in azure.translate_step.call_args_list]
    assert translated == ["Step one", "Step three"]


@pytest.mark.asyncio
async def test_run_single_case_step_indices_ignores_out_of_range():
    cases = [
        {"id": "A", "name": "Alpha", "steps": [
            {"action": "Step one", "expected": "E1"},
            {"action": "Step two", "expected": "E2"},
        ]},
    ]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions()],
        evaluate_side_effect=[{"status": "pass", "reason": "ok"}],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A", step_indices=[1, 99])
    assert [s.action for s in state.test_cases[0].steps] == ["Step two"]


@pytest.mark.asyncio
async def test_run_single_case_step_indices_all_out_of_range_blocks():
    cases = [
        {"id": "A", "name": "Alpha", "steps": [{"action": "Step one", "expected": "E1"}]},
    ]
    azure = _fake_azure(translate_side_effect=[], evaluate_side_effect=[])
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A", step_indices=[99])
    assert state.test_cases[0].status == "blocked"
    azure.translate_step.assert_not_called()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -q`
Expected: 3 FAILED with `TypeError: run_single_case() got an unexpected keyword argument 'step_indices'`

- [ ] **Step 3: Implement the filter in `agent/orchestrator.py`**

Change `run_single_case` (currently ~line 93):

```python
    async def run_single_case(
        self,
        case_id: str,
        plan_key: str = "",
        dry_run: bool = False,
        step_indices: list[int] | None = None,
    ) -> RunState:
        """Run one case (used by `main.py --testcase` and the Manual tab).

        `step_indices` (0-based, original step positions) limits execution to
        those steps; None runs all. The run-state tape contains only the
        executed steps.
        """
        cases = await self.case_source.list_cases(plan_key)
        match = next((c for c in cases if c["id"] == case_id), None)
        if match is None:
            raise KeyError(f"No fixture case with id {case_id!r}")
        plan = await self.case_source.get_plan(plan_key)
        state = new_run_state(plan["key"], plan["name"])
        state.add_case(TestCase(id=match["id"], name=match["name"]))
        state.start_run()
        self.on_update(state)
        try:
            await self._execute_case(
                state, match, dry_run=dry_run, step_indices=step_indices
            )
        finally:
            state.finish()
            self.on_update(state)
        return state
```

Change `_execute_case` (currently ~line 115). The signature gains `step_indices`; the `steps` load and the loop change; everything else stays byte-identical:

```python
    async def _execute_case(
        self,
        state: RunState,
        case: dict[str, Any],
        dry_run: bool = False,
        step_indices: list[int] | None = None,
    ) -> None:
        case_id = case["id"]
        state.start_case(case_id)
        self.on_update(state)

        steps = case.get("steps") or []
        if step_indices is None:
            selected = list(enumerate(steps))
        else:
            selected = [
                (i, steps[i]) for i in sorted(set(step_indices)) if 0 <= i < len(steps)
            ]
        if not selected:
            state.resolve_case(case_id, "blocked")
            self.on_update(state)
            return
```

And the loop body (the `for step_index, step in enumerate(steps):` block becomes — note `tape_index` is what goes to `_execute_step`, because resolve_step indexes the tape, not the original case):

```python
        outcome: str = "pass"
        try:
            for tape_index, (_orig_index, step) in enumerate(selected):
                step_outcome = await self._execute_step(
                    state, case_id, tape_index, step, browser, dry_run=dry_run
                )
                if step_outcome == "fail":
                    outcome = "fail"
                    break
                if step_outcome == "blocked":
                    outcome = "blocked"
                    break
```

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -q`
Expected: all pass (existing tests prove `None` still runs everything).

- [ ] **Step 5: Full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 123 passed.

---

### Task 2: `agent_steps` on the manual mark

**Files:**
- Modify: `agent/manual_state.py` (`ManualMark` dataclass ~line 31, `ManualStore.set_agent` ~line 195)
- Test: `tests/test_manual_state.py` (append at end)

**Interfaces:**
- Produces: `ManualMark.agent_steps: list[int] | None` (default `None`), serialized as `"agent_steps"` in `to_dict()` / restored in `from_dict()` — so it appears under each case's `"manual"` object in the session JSON and survives the snapshot round-trip.
- Produces: `ManualStore.set_agent(plan_key, case_id, agent_status, agent_run_id, agent_steps=_UNSET)` — when `agent_steps` is omitted the stored value is **preserved** (the run-completion callback must not wipe the selection recorded at run start); pass a list or `None` explicitly to overwrite.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_manual_state.py`:

```python
def test_agent_steps_serializes_and_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    store = ManualStore()
    store.build("TP-45", "Plan", [_case_dict()], qmetry_configured=False)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-1", agent_steps=[0, 2])

    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.agent_steps == [0, 2]
    assert case.to_dict()["manual"]["agent_steps"] == [0, 2]

    # snapshot round-trip: a fresh store re-reads the persisted selection
    store2 = ManualStore()
    store2.build("TP-45", "Plan", [_case_dict()], qmetry_configured=False)
    assert store2.get("TP-45").find_case("IRHS-R-01").mark.agent_steps == [0, 2]


def test_set_agent_without_steps_preserves_selection(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    store = ManualStore()
    store.build("TP-45", "Plan", [_case_dict()], qmetry_configured=False)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-1", agent_steps=[1])
    # completion callback omits agent_steps — selection must survive
    store.set_agent("TP-45", "IRHS-R-01", "pass", "run-1")
    assert store.get("TP-45").find_case("IRHS-R-01").mark.agent_steps == [1]
    # explicit None clears it (a full run)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-2", agent_steps=None)
    assert store.get("TP-45").find_case("IRHS-R-01").mark.agent_steps is None
```

If `tests/test_manual_state.py` has no `_case_dict` helper, add one near the top (mirror the file's existing inline case shape — it already builds cases with id `IRHS-R-01`):

```python
def _case_dict():
    return {
        "id": "IRHS-R-01",
        "name": "Create inventory recipe",
        "steps": [
            {"action": "one", "expected": "e1"},
            {"action": "two", "expected": "e2"},
            {"action": "three", "expected": "e3"},
        ],
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py -q`
Expected: FAIL — `set_agent() got an unexpected keyword argument 'agent_steps'`

- [ ] **Step 3: Implement in `agent/manual_state.py`**

Add a module-level sentinel below the type aliases (~line 29):

```python
_UNSET: Any = object()  # sentinel: "don't touch agent_steps"
```

`ManualMark` gains the field + serialization (full replacement of the dataclass body):

```python
@dataclass
class ManualMark:
    status: ManualStatus = "unmarked"
    comment: str = ""
    failed_steps: list[int] = field(default_factory=list)
    agent_status: AgentStatus = None
    agent_run_id: str | None = None
    agent_steps: list[int] | None = None  # indices the last agent run covered; None = all
    pushed_to_qmetry: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "comment": self.comment,
            "failed_steps": list(self.failed_steps),
            "agent_status": self.agent_status,
            "agent_run_id": self.agent_run_id,
            "agent_steps": list(self.agent_steps) if self.agent_steps is not None else None,
            "pushed_to_qmetry": self.pushed_to_qmetry,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ManualMark":
        raw_steps = d.get("agent_steps")
        return cls(
            status=d.get("status", "unmarked"),
            comment=d.get("comment", ""),
            failed_steps=list(d.get("failed_steps", [])),
            agent_status=d.get("agent_status"),
            agent_run_id=d.get("agent_run_id"),
            agent_steps=list(raw_steps) if raw_steps is not None else None,
            pushed_to_qmetry=d.get("pushed_to_qmetry", False),
        )
```

`ManualStore.set_agent` becomes:

```python
    def set_agent(
        self,
        plan_key: str,
        case_id: str,
        agent_status: AgentStatus,
        agent_run_id: str | None,
        agent_steps: list[int] | None = _UNSET,
    ) -> None:
        case = self._require_case(plan_key, case_id)
        case.mark.agent_status = agent_status
        case.mark.agent_run_id = agent_run_id
        if agent_steps is not _UNSET:
            case.mark.agent_steps = list(agent_steps) if agent_steps is not None else None
        self._persist(plan_key, case_id, case.mark)
```

- [ ] **Step 4: Run the module tests, then the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py tests/ -q`
Expected: all pass (125 total).

---

### Task 3: `run-agent` endpoint accepts `{"steps": [...]}`

**Files:**
- Modify: `server.py` (`_run_agent_case` ~line 158, `run_agent_for_case` ~line 226; add `RunAgentBody` next to `MarkBody` ~line 75)
- Test: `tests/test_server.py` (append after `test_run_agent_unknown_case_404`)

**Interfaces:**
- Produces: `POST /manual/{plan}/cases/{case_id}/run-agent` with optional JSON body `{"steps": [0, 1]}`. No body / no key → all steps (existing behavior, existing frontend keeps working). `{"steps": []}` → **422** `"Select at least one step"`. The selection is stored via `MANUAL.set_agent(..., agent_steps=steps)` and passed to `run_single_case(step_indices=steps)`.
- Consumes: Task 1's `run_single_case(..., step_indices=...)`; Task 2's `set_agent(..., agent_steps=...)`.
- Consumes (tests): existing fixtures in `tests/test_server.py`: `client`, `_fake_case_source(cases)`, `server_mod`, and the `patch.object(server_mod, "_run_agent_case", new=AsyncMock())` pattern from `test_run_agent_starts_single_case` (line ~243).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_server.py`:

```python
def test_run_agent_with_steps_records_selection(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [
        {"action": "one", "expected": "e"},
        {"action": "two", "expected": "e"},
        {"action": "three", "expected": "e"},
    ]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")

    fake_run = AsyncMock()
    with patch.object(server_mod, "_run_agent_case", new=fake_run):
        r = client.post("/manual/TP-45/cases/A/run-agent", json={"steps": [0, 2]})
    assert r.status_code == 200
    # selection recorded on the mark for the frontend
    case = server_mod.MANUAL.get("TP-45").find_case("A")
    assert case.mark.agent_steps == [0, 2]
    # selection forwarded to the background runner (5th positional arg)
    assert fake_run.call_args.args[4] == [0, 2]


def test_run_agent_empty_steps_422(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "one", "expected": "e"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")
    r = client.post("/manual/TP-45/cases/A/run-agent", json={"steps": []})
    assert r.status_code == 422
    assert "step" in r.json()["detail"].lower()


def test_run_agent_without_body_still_runs_all(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "one", "expected": "e"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")
    fake_run = AsyncMock()
    with patch.object(server_mod, "_run_agent_case", new=fake_run):
        r = client.post("/manual/TP-45/cases/A/run-agent")
    assert r.status_code == 200
    assert fake_run.call_args.args[4] is None
    assert server_mod.MANUAL.get("TP-45").find_case("A").mark.agent_steps is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -q`
Expected: the 3 new tests FAIL (`IndexError` on `call_args.args[4]` / 200 instead of 422); existing tests pass.

- [ ] **Step 3: Implement in `server.py`**

Add the body model next to `MarkBody` (~line 79):

```python
class RunAgentBody(BaseModel):
    steps: list[int] | None = None
```

`_run_agent_case` gains the pass-through parameter:

```python
async def _run_agent_case(
    run_id: str,
    plan: str,
    case_id: str,
    state: RunState,
    step_indices: list[int] | None = None,
) -> None:
    """Run a single case for the manual view; reflect its result on the mark."""
    try:
        orch = _build_orchestrator(_make_on_update(run_id))
        final = await orch.run_single_case(
            case_id, plan_key=plan, step_indices=step_indices
        )
        RUNS[run_id] = final
        case = next((c for c in final.test_cases if c.id == case_id), None)
        MANUAL.set_agent(plan, case_id, case.status if case else "blocked", run_id)
    except Exception:
        log.exception("Manual agent run %s crashed", run_id)
        state.finish()
        _make_on_update(run_id)(state)
        MANUAL.set_agent(plan, case_id, "blocked", run_id)
```

(Note: the two completion `set_agent` calls deliberately omit `agent_steps` — Task 2's sentinel preserves the selection recorded at start.)

`run_agent_for_case` becomes:

```python
@app.post("/manual/{plan}/cases/{case_id}/run-agent")
async def run_agent_for_case(
    plan: str, case_id: str, body: RunAgentBody | None = None
) -> dict:
    """Kick off a single-case agent run for the manual view.

    Optional body {"steps": [0, 1]} limits the run to those step indices;
    no body runs every step.
    """
    from agent.run_state import new_run_state

    steps = body.steps if body is not None else None
    if steps is not None and len(steps) == 0:
        raise HTTPException(422, "Select at least one step")

    session = MANUAL.get(plan)
    if session is None:
        raise HTTPException(404, f"No manual session for plan {plan!r}; GET it first")
    try:
        case = session.find_case(case_id)
    except KeyError as e:
        raise HTTPException(404, str(e))

    state = new_run_state(plan, session.plan.name)
    state.add_case(TestCase(id=case.id, name=case.name))
    RUNS[state.run_id] = state
    LATEST[state.run_id] = state.to_dict()
    LISTENERS.setdefault(state.run_id, [])
    MANUAL.set_agent(plan, case_id, "running", state.run_id, agent_steps=steps)

    task = asyncio.create_task(
        _run_agent_case(state.run_id, plan, case_id, state, steps)
    )
    TASKS[state.run_id] = task
    return {"run_id": state.run_id}
```

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 128 passed.

---

### Task 4: Frontend — checkboxes, run button, agent chips

**Files:**
- Modify: `frontend/src/hooks/useManualState.js` (`runAgentCase` ~line 48)
- Modify: `frontend/src/components/ManualCase.jsx` (whole component)
- Modify: `frontend/src/tokens.css` (append styles)
- Modify: `frontend/public/fixtures/sample_manual_state.json` (add `"agent_steps": null` to each case's `manual` object)

**Interfaces:**
- Consumes: Task 3's endpoint body; `manual.agent_steps` from the session JSON; existing `useRunState(runId)` hook and `Step` component.
- Produces: `runAgentCase(planKey, caseId, steps = null)` — POSTs JSON `{steps}` only when `steps` is a non-null array.

There is no JS test runner in this repo; the verification steps are `npm run build` (must compile) plus the live smoke test in Task 6.

- [ ] **Step 1: Update `runAgentCase` in `frontend/src/hooks/useManualState.js`**

```js
export async function runAgentCase(planKey, caseId, steps = null) {
  const opts = { method: 'POST' }
  if (Array.isArray(steps)) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify({ steps })
  }
  const res = await fetch(
    `/manual/${encodeURIComponent(planKey)}/cases/${encodeURIComponent(caseId)}/run-agent`,
    opts,
  )
  if (!res.ok) throw new Error(`Run agent failed: ${res.status}`)
  return res.json()
}
```

- [ ] **Step 2: Rework `frontend/src/components/ManualCase.jsx`**

Replace the component body (keep the imports, `STATUSES`, and `cleanMarkup` exactly as they are). Key changes: `useRunState` moves up into `ManualCase` so both the tape and the per-step chips see the agent run; new `agentSel` (checkbox state, all checked by default); `lastRunSteps` remembers which original indices the in-flight/last run covers; `AgentTape` becomes presentational.

```jsx
export default function ManualCase({ plan, testCase, onChanged }) {
  const m = testCase.manual
  const allIndices = testCase.steps.map((_, i) => i)
  const [status, setStatus] = useState(m.status === 'unmarked' ? null : m.status)
  const [comment, setComment] = useState(m.comment || '')
  const [failedSteps, setFailedSteps] = useState(m.failed_steps || [])
  const [saving, setSaving] = useState(false)
  const [agentRunId, setAgentRunId] = useState(m.agent_run_id || null)
  const [runErr, setRunErr] = useState(null)
  // which steps the agent SHOULD run (checkboxes) — all checked by default
  const [agentSel, setAgentSel] = useState(allIndices)
  // which original indices the last-started run covers (for chip mapping)
  const [lastRunSteps, setLastRunSteps] = useState(m.agent_steps ?? null)

  // GUARD: useRunState(null) polls the demo fixture — never let fixture data
  // masquerade as a real agent run.
  const { state: rawAgentState } = useRunState(agentRunId)
  const agentState = agentRunId ? rawAgentState : null

  // Reset local form when switching cases.
  useEffect(() => {
    setStatus(m.status === 'unmarked' ? null : m.status)
    setComment(m.comment || '')
    setFailedSteps(m.failed_steps || [])
    setAgentRunId(m.agent_run_id || null)
    setAgentSel(testCase.steps.map((_, i) => i))
    setLastRunSteps(m.agent_steps ?? null)
  }, [testCase.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const agentRunning =
    agentState?.status === 'running' ||
    (m.agent_status === 'running' && agentState == null)
  const showFlags = status === 'fail' || status === 'blocked'

  // map original step index -> resolved tape step (for the agent chip)
  const executed = lastRunSteps ?? allIndices
  const agentCase =
    agentState?.test_cases?.find((c) => c.id === testCase.id) ?? agentState?.test_cases?.[0]
  const chipByStep = {}
  agentCase?.steps?.forEach((s, i) => {
    const orig = executed[i]
    if (orig != null) chipByStep[orig] = s
  })

  function toggleStep(i) {
    setFailedSteps((prev) => (prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i].sort((a, b) => a - b)))
  }

  function toggleAgentStep(i) {
    setAgentSel((prev) => (prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i].sort((a, b) => a - b)))
  }

  async function save(nextStatus) {
    setStatus(nextStatus)
    setSaving(true)
    try {
      await markCase(plan, testCase.id, {
        status: nextStatus,
        comment,
        failed_steps: nextStatus === 'fail' || nextStatus === 'blocked' ? failedSteps : [],
      })
      await onChanged?.()
    } finally {
      setSaving(false)
    }
  }

  async function handleRunAgent() {
    setRunErr(null)
    const subset = agentSel.length < testCase.steps.length ? agentSel : null
    try {
      const { run_id } = await runAgentCase(plan, testCase.id, subset)
      setLastRunSteps(subset)
      setAgentRunId(run_id)
      await onChanged?.()
    } catch (e) {
      setRunErr(e.message)
    }
  }

  return (
    <section className="manual-case">
      <header className="manual-case-head">
        <span className="stage-head-id">{testCase.id}</span>
        <h1 className="stage-head-title">{testCase.name}</h1>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={agentRunning || agentSel.length === 0}
          onClick={handleRunAgent}
        >
          ▶ Run selected steps with agent
        </button>
        {runErr && <span className="toast-error" role="alert">{runErr}</span>}
      </header>
      <p className="manual-agent-hint">
        The agent starts from the dashboard after login — do unchecked earlier steps by hand first.
      </p>

      <ol className="manual-steps">
        {testCase.steps.map((s, i) => (
          <li key={i} className={`manual-step ${showFlags && failedSteps.includes(i) ? 'flagged ' + status : ''}`}>
            <label className="manual-step-agent">
              <input
                type="checkbox"
                checked={agentSel.includes(i)}
                disabled={agentRunning}
                onChange={() => toggleAgentStep(i)}
              />
              <span>agent</span>
            </label>
            <span className="manual-step-no">{i + 1}</span>
            <div className="manual-step-body">
              <div className="manual-step-action">{cleanMarkup(s.action)}</div>
              {s.expected && (
                <div className="manual-step-expected">{cleanMarkup(s.expected)}</div>
              )}
              {chipByStep[i] && chipByStep[i].status !== 'running' && (
                <span
                  className={`agent-chip ${chipByStep[i].status}`}
                  title={chipByStep[i].evaluation || ''}
                >
                  agent: {chipByStep[i].status}
                </span>
              )}
            </div>
            {showFlags && (
              <label className="manual-step-flag">
                <input
                  type="checkbox"
                  checked={failedSteps.includes(i)}
                  onChange={() => toggleStep(i)}
                />
                <span>problem here</span>
              </label>
            )}
          </li>
        ))}
      </ol>

      <div className="manual-mark-bar">
        {STATUSES.map((s) => (
          <button
            key={s.key}
            type="button"
            className={`mark-btn ${s.key} ${status === s.key ? 'active' : ''}`}
            disabled={saving}
            onClick={() => save(s.key)}
          >
            {s.label}
          </button>
        ))}
      </div>

      {showFlags && (
        <div className="manual-notes">
          <label htmlFor={`note-${testCase.id}`}>Notes</label>
          <textarea
            id={`note-${testCase.id}`}
            value={comment}
            placeholder="What went wrong?"
            onChange={(e) => setComment(e.target.value)}
            onBlur={() => status && save(status)}
          />
        </div>
      )}

      {agentRunId && (
        <AgentTape state={agentState} caseId={testCase.id} onDone={onChanged} />
      )}
    </section>
  )
}

function AgentTape({ state, caseId, onDone }) {
  const firedRef = useRef(false)

  useEffect(() => {
    if (state?.status === 'done' && !firedRef.current) {
      firedRef.current = true
      onDone?.()
    }
  }, [state?.status, onDone])

  const agentCase = state?.test_cases?.find((c) => c.id === caseId) ?? state?.test_cases?.[0]
  const steps = agentCase?.steps ?? []
  return (
    <div className="manual-agent-tape">
      <div className="section-label">Agent run · {agentCase?.status ?? 'running'}</div>
      {steps.map((s, i) => (
        <Step key={i} step={s} />
      ))}
      {steps.length === 0 && <div className="manual-step-expected">Agent is starting…</div>}
    </div>
  )
}
```

(`AgentTape` no longer imports/uses `useRunState`; `useRunState` stays imported at the top for `ManualCase`.)

- [ ] **Step 3: Append styles to `frontend/src/tokens.css`**

```css
/* --- step-selection agent runs (Manual tab) --------------------------- */
.manual-agent-hint {
  font: 400 12px/1.5 var(--font);
  color: var(--muted);
  margin: 4px 0 12px;
}

.manual-step-agent {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font: 400 11px var(--mono);
  color: var(--muted);
  cursor: pointer;
  user-select: none;
}
.manual-step-agent input {
  accent-color: var(--navy);
  cursor: pointer;
}
.manual-step-agent input:focus-visible {
  outline: 2px solid var(--navy-bright);
  outline-offset: 2px;
}

.agent-chip {
  display: inline-block;
  margin-top: 6px;
  padding: 1px 8px;
  border-radius: 999px;
  font: 500 11px var(--mono);
}
.agent-chip.pass { background: var(--green-soft); color: var(--green); }
.agent-chip.fail { background: var(--red-soft); color: var(--red); }
.agent-chip.blocked { background: var(--amber-soft); color: var(--amber); }
```

- [ ] **Step 4: Add `"agent_steps": null` to every `manual` object in `frontend/public/fixtures/sample_manual_state.json`** (keep the rest of each object unchanged; place it after `"agent_run_id"`).

- [ ] **Step 5: Build**

Run: `cd frontend; npm run build`
Expected: Vite build succeeds, `frontend/dist` regenerated. (Corporate SSL bypass is already configured in `frontend/.npmrc` — do not add flags.)

---

### Task 5: FRONTEND.md contract updates

**Files:**
- Modify: `FRONTEND.md` (Manual session state JSON ~line 247; endpoints list ~line 273; Marking UX ~line 281)

- [ ] **Step 1: In the Manual session state JSON example, add one line to the `"manual"` object** after `"agent_run_id": null,`:

```json
        "agent_steps": null,          // step indices the last agent run covered; null = all
```

- [ ] **Step 2: Replace the run-agent endpoint line** in "Endpoints the Manual tab calls":

```markdown
- `POST /manual/{plan}/cases/{id}/run-agent` optional body `{ "steps": [0, 1] }`
  (step indices the agent should execute; omit to run all; empty list → 422) →
  `{run_id}`; tape subscribes via `GET /runs/{id}`.
```

- [ ] **Step 3: Append two bullets to the "Marking UX" section:**

```markdown
- Each step has an "agent" checkbox (all checked by default). "Run selected steps
  with agent" executes only the checked steps in a fresh browser session; a muted
  hint reads "The agent starts from the dashboard after login — do unchecked
  earlier steps by hand first."
- After the run, executed steps show an informational chip — `agent: pass` /
  `agent: fail` (evaluator reason on hover). The chip never sets the case verdict;
  only the tester's Pass / Fail / Blocked mark does.
```

- [ ] **Step 4: Full suite** (test_run_state asserts contract parity — must stay green)

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 128 passed.

---

### Task 6: Restart server + live smoke test

**Files:** none (operations only)

- [ ] **Step 1: Restart the server so it loads the new code**

```powershell
$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conn) { $conn | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -Confirm:$false }; Start-Sleep 1 }
& "C:\Users\rsantos\AI\QA\scripts\serve.cmd"
```

Expected: `Invoke-WebRequest http://127.0.0.1:8000/ -UseBasicParsing` returns 200 within ~15s.

- [ ] **Step 2: API smoke test — run steps 1–2 of the fixture case via the new body**

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/manual/1ZwYH2ObF7AGZa" | Out-Null
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/manual/1ZwYH2ObF7AGZa/cases/SOUSCLOUD-TC-2395/run-agent" -Method Post -Body '{"steps":[0]}' -ContentType "application/json"
$r.run_id
```

Expected: a `run-…` id. Poll `GET /runs/{id}` until `status: done`; the run's single test case must contain exactly **1 step** in its tape. Then `GET /manual/1ZwYH2ObF7AGZa` must show that case's `manual.agent_steps == [0]` and an `agent_status` of pass/fail (informational), with `status` still whatever the tester last marked.

- [ ] **Step 3: Browser check** — open `http://127.0.0.1:8000/?cycle=1ZwYH2ObF7AGZa`, Manual tab: checkboxes render checked, untick some, button disables at zero selection, run streams the tape, chips appear on executed steps only, mark buttons still set the verdict.
