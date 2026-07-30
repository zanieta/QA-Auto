# Per-step QMetry results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a case runs, write each test step's real status (Pass/Fail/Blocked) into QMetry — into the existing execution or a newly-created one — as an explicit, gated commit from both the Manual tab and agent runs.

**Architecture:** Three new methods on `QMetryClient` (list step executions, post one step result, create an execution) plus one shared writer helper `write_case_execution(...)` in `agent/qmetry.py`. Both the Manual push and a new agent-run push endpoint call the helper. A `QMETRY_EXECUTION_MODE` env flag chooses edit-in-place (default) vs create-new-run-in-same-cycle. Writing is never automatic — a plain run only reads + reports.

**Tech Stack:** Python 3.14, async, `httpx` (mocked in tests), FastAPI, pytest + `pytest-asyncio`, `unittest.mock.AsyncMock`. Frontend: React + Vite (hand-written CSS from Duke tokens — see FRONTEND.md).

## Global Constraints

- Backend: async throughout, `httpx` for async HTTP, type hints, docstrings, `logging` not `print`.
- Never hardcode credentials, plan ids, project keys, or URLs — env or request args only. The frontend gets NO secrets and talks ONLY to `server.py`.
- QMetry host `https://qtmcloud.qmetry.com`, base `/rest/api/latest`, auth via `apiKey` request header (already in `QMetryClient`).
- QMetry execution status names come from `get_execution_results()` and are matched case-insensitively (`status.lower()`), reusing `QMetryClient._exec_result_cache`.
- QMetry comment limit is 4000 chars (existing `post_execution_result` truncates with `comment[:4000]`).
- Tests never hit the network or launch a browser — mock `_request` / `QMetryClient`.
- run_state shape changes require updating FRONTEND.md + the frontend hook in the SAME change. (Only Task 8 touches UI; no run_state shape change is planned.)
- Endpoint shapes for step executions / create-execution are UNVERIFIED against the live API. Task 1 (read-only probe) captures them. Tasks 2–3 are written against the shapes documented here; if the probe contradicts them, update Tasks 2–3 before coding and note the change.

---

### Task 1: Read-only probe — capture QMetry step-execution + create-execution shapes

**Files:**
- Modify: `scripts/qmetry_probe.py` (add probe functions; keep it a dev tool, not imported by the app)

**Interfaces:**
- Consumes: `agent.qmetry.QMetryClient` (existing), env `QMETRY_API_KEY`, a real cycle id/key and one test-case execution id (pass as CLI args).
- Produces: printed JSON shapes for (a) listing a test-case execution's step executions, (b) the field that holds a step execution's id + its result slot, (c) whether step-execution order matches our flattened step order, (d) the create-execution request/response shape. NO code the app imports.

This task is discovery only — it does not change app behavior. It exists so Tasks 2–3 are written against real shapes, matching how the rest of `agent/qmetry.py` was built.

- [ ] **Step 1: Add a `probe_step_executions` function**

Add to `scripts/qmetry_probe.py`. It must only READ (GET/POST-search); it must NOT create or PUT anything.

```python
async def probe_step_executions(cycle_id: str, exec_id: str) -> None:
    """Print the step-execution rows for one test-case execution.

    Discovers: the endpoint path, the per-row id field, and the result slot.
    READ-ONLY.
    """
    from agent.qmetry import QMetryClient
    import json

    client = QMetryClient()
    # Candidate paths seen in QMetry-for-Jira; print whichever responds.
    candidates = [
        f"/testcycles/{cycle_id}/testcase-executions/{exec_id}/teststep-executions",
        f"/testcycles/{cycle_id}/testcase-executions/{exec_id}/teststep-executions/search",
    ]
    for path in candidates:
        for method in ("GET", "POST"):
            try:
                body = {} if method == "POST" else None
                data = await client._request(method, path, json=body)
                print(f"\n=== {method} {path} ===")
                print(json.dumps(data, indent=2)[:4000])
            except Exception as e:  # noqa: BLE001 - probe prints and continues
                print(f"{method} {path} -> {e}")
```

- [ ] **Step 2: Add a `probe_create_execution` function (dry — prints intended call only)**

```python
async def probe_create_execution(cycle_id: str, tc_id: str, version_no: int) -> None:
    """Print the intended create-execution call WITHOUT sending it.

    Creating an execution is a WRITE. This function only prints what it WOULD
    send so Roman can approve before any real write happens.
    """
    path = f"/testcycles/{cycle_id}/testcase-executions"
    body = {"tcId": tc_id, "tcVersionNo": version_no}
    print(f"WOULD POST {path} with body {body}")
    print("Run the live create only after Roman confirms the shape above.")
```

- [ ] **Step 3: Wire both into the probe's `__main__` arg handling**

Follow the file's existing arg-dispatch style (read the file first). Add subcommands `step-execs <cycle> <exec>` and `create-exec-dry <cycle> <tc> <ver>`.

- [ ] **Step 4: Run the read-only probe against a real execution**

```powershell
.venv\Scripts\python.exe scripts\qmetry_probe.py step-execs <cycleId> <execId>
```
Expected: JSON printed for at least one candidate path. Record in the plan/spec: the working path, the step-execution id field name, the result-slot field name, and whether row order matches our flattened step order.

- [ ] **Step 5: Reconcile Tasks 2–3 with findings**

If the real path or field names differ from what Tasks 2–3 assume (`teststep-executions`, `id`, `executionResultId`), edit Tasks 2–3 to match BEFORE implementing them. No commit (probe is a dev tool; nothing app-facing changed).

---

### Task 2: `QMetryClient.get_test_step_executions` + `post_step_execution_result`

**Files:**
- Modify: `agent/qmetry.py` (add two methods to `QMetryClient`, after `post_execution_result`)
- Test: `tests/test_qmetry.py`

**Interfaces:**
- Consumes: `QMetryClient._request`, `QMetryClient.get_execution_results`, `QMetryClient._exec_result_cache` (existing).
- Produces:
  - `async def get_test_step_executions(self, cycle_id: str, exec_id: int) -> list[dict]` — ordered step-execution rows; each row has key `id` (the step-execution id) — an ordered list as returned by the API.
  - `async def post_step_execution_result(self, cycle_id: str, exec_id: int, step_exec_id: int, status: str, comment: str | None = None) -> None` — sets one step's result; unknown status name → log + skip (no PUT), mirroring `post_execution_result`.

(Path/field names below assume Task 1 confirmed `teststep-executions`, row id `id`, result field `executionResultId`. If Task 1 found otherwise, use the confirmed names.)

- [ ] **Step 1: Write the failing test for `get_test_step_executions`**

Add to `tests/test_qmetry.py`:

```python
@pytest.mark.asyncio
async def test_get_test_step_executions_returns_ordered_rows():
    page = {
        "total": 2,
        "data": [
            {"id": 501, "seqNo": 1},
            {"id": 502, "seqNo": 2},
        ],
    }
    client = QMetryClient(api_key="key", project_id="10022")
    with patch.object(client, "_request", new=AsyncMock(return_value=page)):
        rows = await client.get_test_step_executions("CY-1", 100)
    assert [r["id"] for r in rows] == [501, 502]
```

- [ ] **Step 2: Write the failing test for `post_step_execution_result`**

```python
@pytest.mark.asyncio
async def test_post_step_execution_result_puts_with_result_id():
    client = QMetryClient(api_key="key", project_id="10022")
    client._exec_result_cache = {"pass": 1, "fail": 2, "blocked": 3}
    mock_request = AsyncMock(return_value={})
    with patch.object(client, "_request", new=mock_request):
        await client.post_step_execution_result("CY-1", 100, 501, "pass", "looks good")
    args, kwargs = mock_request.call_args
    assert args[0] == "PUT"
    assert "teststep-executions/501" in args[1]
    assert kwargs["json"]["executionResultId"] == 1
    assert kwargs["json"]["comment"] == "looks good"


@pytest.mark.asyncio
async def test_post_step_execution_result_skips_unknown_status():
    client = QMetryClient(api_key="key", project_id="10022")
    client._exec_result_cache = {"pass": 1, "fail": 2}
    mock_request = AsyncMock(return_value={})
    with patch.object(client, "_request", new=mock_request):
        await client.post_step_execution_result("CY-1", 100, 501, "blocked")
    mock_request.assert_not_called()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qmetry.py::test_get_test_step_executions_returns_ordered_rows tests/test_qmetry.py::test_post_step_execution_result_puts_with_result_id tests/test_qmetry.py::test_post_step_execution_result_skips_unknown_status -v`
Expected: FAIL — `AttributeError: 'QMetryClient' object has no attribute 'get_test_step_executions'`.

- [ ] **Step 4: Implement both methods**

Add to `QMetryClient` in `agent/qmetry.py`, immediately after `post_execution_result`:

```python
    async def get_test_step_executions(
        self, cycle_id: str, exec_id: int
    ) -> list[dict[str, Any]]:
        """GET step-execution rows for one test-case execution, in order.

        Each row carries its own step-execution ``id`` plus a result slot. Order
        matches the flattened step order the CaseSource produced (verified via
        scripts/qmetry_probe.py, 2026-07-20).
        """
        data = await self._request(
            "GET",
            f"/testcycles/{cycle_id}/testcase-executions/{exec_id}/teststep-executions",
        )
        rows = data if isinstance(data, list) else data.get("data", [])
        return rows

    async def post_step_execution_result(
        self,
        cycle_id: str,
        exec_id: int,
        step_exec_id: int,
        status: str,
        comment: str | None = None,
    ) -> None:
        """PUT one step's result. Unknown status name → log + skip (no PUT)."""
        if not self._exec_result_cache:
            results = await self.get_execution_results()
            self._exec_result_cache = {r["name"].lower(): r["id"] for r in results}

        result_id = self._exec_result_cache.get(status.lower())
        if result_id is None:
            log.warning("Unknown QMetry step status %r; skipping post", status)
            return

        body: dict[str, Any] = {"executionResultId": result_id}
        if comment:
            body["comment"] = comment[:4000]

        await self._request(
            "PUT",
            f"/testcycles/{cycle_id}/testcase-executions/{exec_id}"
            f"/teststep-executions/{step_exec_id}",
            json=body,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qmetry.py -v`
Expected: PASS (all, including the three new tests).

- [ ] **Step 6: Commit** (repo is NOT under git — SKIP. Note completion in the task list instead.)

---

### Task 3: `QMetryClient.create_execution`

**Files:**
- Modify: `agent/qmetry.py` (add one method to `QMetryClient`)
- Test: `tests/test_qmetry.py`

**Interfaces:**
- Consumes: `QMetryClient._request`.
- Produces: `async def create_execution(self, cycle_id: str, tc_id: str, version_no: int) -> int` — creates a new execution run of the case in the cycle, returns the new execution id.

(Request body / response id path below assume Task 1's `create-exec-dry` shape. Use the confirmed shape if it differs.)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_create_execution_returns_new_exec_id():
    client = QMetryClient(api_key="key", project_id="10022")
    resp = {"data": {"id": 909, "tcId": "tc1"}}
    mock_request = AsyncMock(return_value=resp)
    with patch.object(client, "_request", new=mock_request):
        new_id = await client.create_execution("CY-1", "tc1", 2)
    assert new_id == 909
    args, kwargs = mock_request.call_args
    assert args[0] == "POST"
    assert args[1] == "/testcycles/CY-1/testcase-executions"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qmetry.py::test_create_execution_returns_new_exec_id -v`
Expected: FAIL — no attribute `create_execution`.

- [ ] **Step 3: Implement the method**

Add to `QMetryClient` after `post_step_execution_result`:

```python
    async def create_execution(
        self, cycle_id: str, tc_id: str, version_no: int
    ) -> int:
        """Create a fresh execution run of a test case inside an EXISTING cycle.

        Returns the new test-case-execution id. Used only by
        QMETRY_EXECUTION_MODE=create — the app never creates whole test cycles.
        """
        data = await self._request(
            "POST",
            f"/testcycles/{cycle_id}/testcase-executions",
            json={"tcId": tc_id, "tcVersionNo": version_no},
        )
        payload = data.get("data", data) if isinstance(data, dict) else data
        return payload["id"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qmetry.py::test_create_execution_returns_new_exec_id -v`
Expected: PASS.

- [ ] **Step 5: Commit** (repo NOT under git — SKIP.)

---

### Task 4: The shared `write_case_execution` helper

**Files:**
- Modify: `agent/qmetry.py` (add a module-level dataclass `WriteResult` and an async function `write_case_execution`, after the `QMetryClient` class, before `QMetryCaseSource`)
- Test: `tests/test_qmetry.py`

**Interfaces:**
- Consumes: `QMetryClient.create_execution`, `.get_test_step_executions`, `.post_step_execution_result`, `.post_execution_result` (Tasks 2–3 + existing).
- Produces:
  - `@dataclass class WriteResult: exec_id: int; steps_written: int; errors: list[dict]`
  - `async def write_case_execution(client, *, cycle_id, execution_id, tc_id, version_no, case_status, step_results, mode="edit", comment=None) -> WriteResult`
    - `step_results: dict[int, tuple[str, str | None]]` — flattened-step-index → (status, comment). Only the steps we have a status for.
    - `mode: str` — `"edit"` uses `execution_id`; `"create"` calls `create_execution` and uses the new id.
    - Maps `step_results` onto step-execution rows BY POSITION. A per-step post error is collected into `errors`, NOT raised. Then posts the case-level result. Returns `WriteResult`.

- [ ] **Step 1: Write the failing test — edit mode maps by position**

```python
@pytest.mark.asyncio
async def test_write_case_execution_edit_maps_steps_by_position():
    from agent.qmetry import write_case_execution

    client = MagicMock()
    client.get_test_step_executions = AsyncMock(return_value=[
        {"id": 501}, {"id": 502}, {"id": 503},
    ])
    client.post_step_execution_result = AsyncMock(return_value=None)
    client.post_execution_result = AsyncMock(return_value=None)
    client.create_execution = AsyncMock()

    result = await write_case_execution(
        client,
        cycle_id="CY-1", execution_id=100, tc_id="tc1", version_no=1,
        case_status="fail",
        step_results={0: ("pass", None), 2: ("fail", "broke")},
        mode="edit", comment="case note",
    )

    # create_execution NOT called in edit mode
    client.create_execution.assert_not_awaited()
    assert result.exec_id == 100
    assert result.steps_written == 2
    # step index 0 -> row 501 pass; step index 2 -> row 503 fail
    calls = {c.args[2]: (c.args[3], c.args[4]) for c in client.post_step_execution_result.await_args_list}
    assert calls[501] == ("pass", None)
    assert calls[503] == ("fail", "broke")
    assert 502 not in calls  # index 1 had no status -> untouched
    # case-level result posted
    ca, ck = client.post_execution_result.await_args
    assert ck.get("status", ca[2] if len(ca) > 2 else None) == "fail"
```

Note: the assertion reads `post_step_execution_result` positional args `(cycle_id, exec_id, step_exec_id, status, comment)` — implement the call positionally to match.

- [ ] **Step 2: Write the failing test — create mode uses new exec id**

```python
@pytest.mark.asyncio
async def test_write_case_execution_create_uses_new_exec_id():
    from agent.qmetry import write_case_execution

    client = MagicMock()
    client.create_execution = AsyncMock(return_value=909)
    client.get_test_step_executions = AsyncMock(return_value=[{"id": 700}])
    client.post_step_execution_result = AsyncMock(return_value=None)
    client.post_execution_result = AsyncMock(return_value=None)

    result = await write_case_execution(
        client,
        cycle_id="CY-1", execution_id=100, tc_id="tc1", version_no=1,
        case_status="pass",
        step_results={0: ("pass", None)},
        mode="create",
    )

    client.create_execution.assert_awaited_once_with("CY-1", "tc1", 1)
    assert result.exec_id == 909
    # step + case results posted against the NEW exec id 909, not 100
    assert client.get_test_step_executions.await_args.args == ("CY-1", 909)
    assert client.post_step_execution_result.await_args.args[1] == 909
```

- [ ] **Step 3: Write the failing test — a per-step error is non-fatal**

```python
@pytest.mark.asyncio
async def test_write_case_execution_step_error_is_non_fatal():
    from agent.qmetry import write_case_execution, QMetryError

    client = MagicMock()
    client.create_execution = AsyncMock()
    client.get_test_step_executions = AsyncMock(return_value=[{"id": 501}, {"id": 502}])
    async def _post_step(cycle_id, exec_id, step_exec_id, status, comment=None):
        if step_exec_id == 501:
            raise QMetryError("boom")
    client.post_step_execution_result = AsyncMock(side_effect=_post_step)
    client.post_execution_result = AsyncMock(return_value=None)

    result = await write_case_execution(
        client,
        cycle_id="CY-1", execution_id=100, tc_id="tc1", version_no=1,
        case_status="pass",
        step_results={0: ("pass", None), 1: ("pass", None)},
        mode="edit",
    )
    assert result.steps_written == 1          # 502 succeeded
    assert len(result.errors) == 1            # 501 failed
    assert result.errors[0]["step_exec_id"] == 501
    client.post_execution_result.assert_awaited_once()  # case-level still posted
```

- [ ] **Step 4: Run the three tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qmetry.py -k write_case_execution -v`
Expected: FAIL — `cannot import name 'write_case_execution'`.

- [ ] **Step 5: Implement `WriteResult` + `write_case_execution`**

Add to `agent/qmetry.py` after the `QMetryClient` class (before `class QMetryCaseSource`). `dataclass` is already importable via `from dataclasses import dataclass` — add that import at the top if absent.

```python
from dataclasses import dataclass


@dataclass
class WriteResult:
    exec_id: int
    steps_written: int
    errors: list[dict]


async def write_case_execution(
    client: "QMetryClient",
    *,
    cycle_id: str,
    execution_id: int,
    tc_id: str,
    version_no: int,
    case_status: str,
    step_results: dict[int, tuple[str, str | None]],
    mode: str = "edit",
    comment: str | None = None,
) -> WriteResult:
    """Write per-step + case-level results for one execution.

    mode="edit": write into ``execution_id``. mode="create": create a fresh
    execution run in the SAME cycle and write into it. ``step_results`` maps a
    flattened step index to (status, comment); it is mapped onto the execution's
    step rows BY POSITION. A per-step post failure is collected into
    ``errors`` (never raised); the case-level result is always attempted.
    """
    exec_id = execution_id
    if mode == "create":
        exec_id = await client.create_execution(cycle_id, tc_id, version_no)

    rows = await client.get_test_step_executions(cycle_id, exec_id)
    steps_written = 0
    errors: list[dict] = []
    for idx, (status, step_comment) in sorted(step_results.items()):
        if idx < 0 or idx >= len(rows):
            errors.append({"step_index": idx, "error": "no matching step row"})
            continue
        step_exec_id = rows[idx]["id"]
        try:
            await client.post_step_execution_result(
                cycle_id, exec_id, step_exec_id, status, step_comment
            )
            steps_written += 1
        except QMetryError as e:
            errors.append({"step_index": idx, "step_exec_id": step_exec_id, "error": str(e)})

    await client.post_execution_result(
        cycle_id=cycle_id, execution_id=exec_id, status=case_status, comment=comment
    )
    return WriteResult(exec_id=exec_id, steps_written=steps_written, errors=errors)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qmetry.py -v`
Expected: PASS (whole module).

- [ ] **Step 7: Commit** (repo NOT under git — SKIP.)

---

### Task 5: Env flag `QMETRY_EXECUTION_MODE` + `.env.example`

**Files:**
- Modify: `.env.example` (document the new flag)
- Modify: `server.py` (add a `_qmetry_execution_mode()` helper near `_qmetry_configured`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `def _qmetry_execution_mode() -> str` in `server.py` — returns `"create"` if `QMETRY_EXECUTION_MODE` (case-insensitive) is `create`, else `"edit"` (the safe default for any other/missing value).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_qmetry_execution_mode_defaults_to_edit(monkeypatch):
    monkeypatch.delenv("QMETRY_EXECUTION_MODE", raising=False)
    assert server_mod._qmetry_execution_mode() == "edit"


def test_qmetry_execution_mode_create(monkeypatch):
    monkeypatch.setenv("QMETRY_EXECUTION_MODE", "CREATE")
    assert server_mod._qmetry_execution_mode() == "create"


def test_qmetry_execution_mode_garbage_is_edit(monkeypatch):
    monkeypatch.setenv("QMETRY_EXECUTION_MODE", "banana")
    assert server_mod._qmetry_execution_mode() == "edit"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k qmetry_execution_mode -v`
Expected: FAIL — no attribute `_qmetry_execution_mode`.

- [ ] **Step 3: Implement the helper**

Add to `server.py` right after `_qmetry_configured`:

```python
def _qmetry_execution_mode() -> str:
    """edit (default) writes results into the case's existing execution;
    create makes a fresh execution run in the same cycle each push."""
    mode = os.environ.get("QMETRY_EXECUTION_MODE", "edit").strip().lower()
    return "create" if mode == "create" else "edit"
```

- [ ] **Step 4: Document the flag in `.env.example`**

Under the QMetry section add:

```
# edit  = write results into the case's existing execution (default)
# create = create a new execution run in the same cycle on each push
QMETRY_EXECUTION_MODE=edit
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k qmetry_execution_mode -v`
Expected: PASS.

- [ ] **Step 6: Commit** (repo NOT under git — SKIP.)

---

### Task 6: Upgrade Manual-tab push to write per-step via the helper

**Files:**
- Modify: `server.py` (`push_manual_to_qmetry`, lines ~439–487)
- Test: `tests/test_server.py` (extend existing `test_push_qmetry_*`)

**Interfaces:**
- Consumes: `write_case_execution`, `_qmetry_execution_mode`, `ManualCase` fields `mark.step_marks`, `mark.status`, `execution_id`, `execution_cycle_id`, plus the tc id/version needed for create mode.
- Produces: same JSON response `{"pushed", "skipped", "errors"}`, but each pushed case now also has per-step results written.

Note on tc id/version for create mode: `ManualCase` today does not carry `tc_id`/`version_no` (they aren't in `to_dict`, but `list_cases` has them). Add two server-side-only fields to `ManualCase` in `agent/manual_state.py` — `tc_id: str | None = None`, `version_no: int = 1` — populated in `ManualStore.build` from `rc.get("_qmetry_tc_id")` / `rc.get("_qmetry_version_no")`, and have `QMetryCaseSource._hydrate` include those two private keys. This is required for create mode; edit mode ignores them.

- [ ] **Step 1: Add the private tc id/version to the QMetry case dict**

In `agent/qmetry.py` `_hydrate`, add to the returned dict:

```python
                "_qmetry_tc_id": tc_id,
                "_qmetry_version_no": version_no,
```

- [ ] **Step 2: Carry them onto `ManualCase`**

In `agent/manual_state.py`: add fields to `ManualCase`:

```python
    tc_id: str | None = None  # server-side only — for create-mode execution
    version_no: int = 1       # server-side only
```

and in `ManualStore.build`, when constructing each `ManualCase`, add:

```python
                    tc_id=rc.get("_qmetry_tc_id"),
                    version_no=rc.get("_qmetry_version_no", 1),
```

- [ ] **Step 3: Write the failing test — Manual push writes per-step**

Extend `tests/test_server.py`. Add a new test modeled on `test_push_qmetry_posts_marked_cases`:

```python
def test_push_qmetry_writes_per_step(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [
        {"id": "A", "name": "Case A",
         "steps": [{"action": "s1", "expected": "e1"}, {"action": "s2", "expected": "e2"}],
         "_qmetry_execution_id": 111, "_qmetry_cycle_id": "CYC-1",
         "_qmetry_tc_id": "tc1", "_qmetry_version_no": 1},
    ]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    monkeypatch.setattr(server_mod, "_qmetry_execution_mode", lambda: "edit")
    client.get("/manual/TP-45")
    client.post("/manual/TP-45/cases/A/steps/0/mark", json={"status": "pass"})
    client.post("/manual/TP-45/cases/A/steps/1/mark", json={"status": "fail", "note": "bad"})

    called = {}
    async def _writer(clientobj, **kwargs):
        called.update(kwargs)
        from agent.qmetry import WriteResult
        return WriteResult(exec_id=111, steps_written=2, errors=[])
    monkeypatch.setattr("agent.qmetry.write_case_execution", _writer)

    fake = AsyncMock(); fake.aclose = AsyncMock()
    monkeypatch.setattr("agent.qmetry.QMetryClient", lambda **kw: fake)

    r = client.post("/manual/TP-45/push-qmetry")
    assert r.status_code == 200
    assert r.json()["pushed"] == ["A"]
    assert called["cycle_id"] == "CYC-1"
    assert called["execution_id"] == 111
    assert called["mode"] == "edit"
    # step 0 pass, step 1 fail — mapped by flattened index
    assert called["step_results"][0][0] == "pass"
    assert called["step_results"][1][0] == "fail"
    assert called["case_status"] == "fail"  # derived: fail > pass
```

- [ ] **Step 4: Run it to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py::test_push_qmetry_writes_per_step -v`
Expected: FAIL — `write_case_execution` not called / `step_results` KeyError.

- [ ] **Step 5: Rewrite `push_manual_to_qmetry` to use the helper**

Replace the per-case body in `server.py` `push_manual_to_qmetry`. Build `step_results` from `case.mark.step_marks` (keys are stringified indices; note per step becomes the step comment), pick mode from `_qmetry_execution_mode()`, and call the helper:

```python
    from agent.qmetry import QMetryClient, QMetryError, write_case_execution

    client = QMetryClient()
    mode = _qmetry_execution_mode()
    pushed: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []
    try:
        for case in marked:
            if case.execution_id is None:
                skipped.append(case.id)
                continue
            step_results = {
                int(i): (sm["status"], sm.get("note") or None)
                for i, sm in case.mark.step_marks.items()
                if sm.get("status") in ("pass", "fail", "blocked")
            }
            try:
                await write_case_execution(
                    client,
                    cycle_id=case.execution_cycle_id or plan,
                    execution_id=case.execution_id,
                    tc_id=case.tc_id or case.id,
                    version_no=case.version_no,
                    case_status=case.mark.status,
                    step_results=step_results,
                    mode=mode,
                    comment=compose_comment(case) or None,
                )
                MANUAL.mark_pushed(plan, case.id)
                pushed.append(case.id)
            except QMetryError as e:
                errors.append({"case": case.id, "error": str(e)})
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass

    return {"pushed": pushed, "skipped": skipped, "errors": errors}
```

- [ ] **Step 6: Run the new test AND the existing manual-push tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k push_qmetry -v`
Expected: PASS for the new `test_push_qmetry_writes_per_step`.

NOTE: `test_push_qmetry_posts_marked_cases`, `_per_case_error_is_non_fatal`, and `_uses_internal_cycle_id` assert on `post_execution_result` directly — they will now break because the endpoint calls `write_case_execution` instead. Update those three tests to patch/assert `write_case_execution` (assert `mode`, `cycle_id`, `execution_id`, `case_status`, and that a `QMetryError` from the writer lands in `errors` and does NOT mark pushed). Keep their original intent (skip-no-exec-id, non-fatal-error, internal-cycle-id) intact.

- [ ] **Step 7: Run the whole server suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -q`
Expected: PASS.

- [ ] **Step 8: Commit** (repo NOT under git — SKIP.)

---

### Task 7: New gated `POST /runs/{id}/push-qmetry` for agent runs + `main.py --push-qmetry`

**Files:**
- Modify: `server.py` (new endpoint after `cancel_run`)
- Modify: `main.py` (add `--push-qmetry` flag + a push helper)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `RUNS[run_id]` (a finished `RunState`), `write_case_execution`, `_qmetry_execution_mode`, `_qmetry_configured`, and — to resolve each case's QMetry cycle/exec/tc ids — `_make_case_source().list_cases(plan_key)`. The RunState carries `plan.key`; match cases by `case.id`.
- Produces:
  - `POST /runs/{run_id}/push-qmetry` → `{"pushed": [...], "skipped": [...], "errors": [...]}`. Gated: 409 if QMetry not configured, 409 if the run is not `done`, 404 if unknown run id.
  - `main.py --push-qmetry`: after a plan run finishes, resolve ids from the case source and push each case's run_state step statuses.

Mapping run_state → step_results: for case `c` in `RunState.test_cases`, `c.steps[i].status` is the flattened step status (run_state tape == flattened order for a full run). `step_results = {i: (step.status, step.evaluation) for i, step in enumerate(c.steps) if step.status in ("pass","fail","blocked")}`. The QMetry cycle/exec/tc ids come from the matching case dict in `list_cases` (`_qmetry_cycle_id`, `_qmetry_execution_id`, `_qmetry_tc_id`, `_qmetry_version_no`).

- [ ] **Step 1: Write the failing test for the endpoint (gating + happy path)**

Add to `tests/test_server.py`:

```python
def test_run_push_qmetry_requires_done_run(client, monkeypatch):
    from agent.run_state import new_run_state
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    state = new_run_state("CY-1", "Cycle 1")
    state.start_run()  # running, not done
    server_mod.RUNS[state.run_id] = state
    r = client.post(f"/runs/{state.run_id}/push-qmetry")
    assert r.status_code == 409


def test_run_push_qmetry_writes_each_case(client, monkeypatch):
    from agent.run_state import new_run_state, TestCase, Step
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    monkeypatch.setattr(server_mod, "_qmetry_execution_mode", lambda: "edit")

    state = new_run_state("CY-1", "Cycle 1")
    case = TestCase(id="A", name="Case A")
    case.steps = [Step(action="s1", detail="", status="pass", evaluation="ok"),
                  Step(action="s2", detail="", status="fail", evaluation="bad")]
    case.status = "fail"
    state.add_case(case)
    state.start_run(); state.finish()
    server_mod.RUNS[state.run_id] = state

    src_cases = [{"id": "A", "name": "Case A", "steps": [{}, {}],
                  "_qmetry_execution_id": 111, "_qmetry_cycle_id": "CYC-1",
                  "_qmetry_tc_id": "tc1", "_qmetry_version_no": 1}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(src_cases))

    calls = []
    async def _writer(clientobj, **kwargs):
        calls.append(kwargs)
        from agent.qmetry import WriteResult
        return WriteResult(exec_id=111, steps_written=2, errors=[])
    monkeypatch.setattr("agent.qmetry.write_case_execution", _writer)
    fake = AsyncMock(); fake.aclose = AsyncMock()
    monkeypatch.setattr("agent.qmetry.QMetryClient", lambda **kw: fake)

    r = client.post(f"/runs/{state.run_id}/push-qmetry")
    assert r.status_code == 200
    assert r.json()["pushed"] == ["A"]
    assert calls[0]["execution_id"] == 111
    assert calls[0]["case_status"] == "fail"
    assert calls[0]["step_results"][0][0] == "pass"
    assert calls[0]["step_results"][1][0] == "fail"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k run_push_qmetry -v`
Expected: FAIL — 404 (endpoint doesn't exist).

- [ ] **Step 3: Implement the endpoint**

Add to `server.py` after `cancel_run`:

```python
@app.post("/runs/{run_id}/push-qmetry")
async def push_run_to_qmetry(run_id: str) -> dict:
    """Gated: write a finished run's per-step results to QMetry.

    Never automatic — the console/CLI calls this explicitly. 409 unless QMetry
    is configured and the run is done.
    """
    if not _qmetry_configured():
        raise HTTPException(409, "QMetry is not configured — set QMETRY_API_KEY first")
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "Unknown run id")
    if state.status != "done":
        raise HTTPException(409, "Run is not finished yet")

    source = _make_case_source()
    src_cases = {c["id"]: c for c in await source.list_cases(state.plan.key)}

    from agent.qmetry import QMetryClient, QMetryError, write_case_execution

    client = QMetryClient()
    mode = _qmetry_execution_mode()
    pushed: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []
    try:
        for case in state.test_cases:
            src = src_cases.get(case.id)
            if src is None or src.get("_qmetry_execution_id") is None:
                skipped.append(case.id)
                continue
            step_results = {
                i: (s.status, s.evaluation)
                for i, s in enumerate(case.steps)
                if s.status in ("pass", "fail", "blocked")
            }
            try:
                await write_case_execution(
                    client,
                    cycle_id=src.get("_qmetry_cycle_id") or state.plan.key,
                    execution_id=src["_qmetry_execution_id"],
                    tc_id=src.get("_qmetry_tc_id") or case.id,
                    version_no=src.get("_qmetry_version_no", 1),
                    case_status=case.status,
                    step_results=step_results,
                    mode=mode,
                )
                pushed.append(case.id)
            except QMetryError as e:
                errors.append({"case": case.id, "error": str(e)})
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass
    return {"pushed": pushed, "skipped": skipped, "errors": errors}
```

- [ ] **Step 4: Run the endpoint tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k run_push_qmetry -v`
Expected: PASS.

- [ ] **Step 5: Add `--push-qmetry` to `main.py`**

In `parse_args`, add:

```python
    p.add_argument("--push-qmetry", action="store_true",
                   help="After a plan run, write per-step results to QMetry")
```

In `_run`, after the plan run finishes and before the report, add (only for `args.plan`, and never in `--dry-run`):

```python
    if args.plan and args.push_qmetry and not args.dry_run:
        from agent.case_source import CaseSource  # noqa: F401
        from agent.qmetry import QMetryClient, QMetryError, QMetryCaseSource, write_case_execution

        mode = os.environ.get("QMETRY_EXECUTION_MODE", "edit").strip().lower()
        mode = "create" if mode == "create" else "edit"
        source = QMetryCaseSource()
        src_cases = {c["id"]: c for c in await source.list_cases(args.plan)}
        client = QMetryClient()
        for case in state.test_cases:
            src = src_cases.get(case.id)
            if src is None or src.get("_qmetry_execution_id") is None:
                print(f"skip {case.id}: no QMetry execution id")
                continue
            step_results = {
                i: (s.status, s.evaluation)
                for i, s in enumerate(case.steps)
                if s.status in ("pass", "fail", "blocked")
            }
            try:
                r = await write_case_execution(
                    client,
                    cycle_id=src.get("_qmetry_cycle_id") or args.plan,
                    execution_id=src["_qmetry_execution_id"],
                    tc_id=src.get("_qmetry_tc_id") or case.id,
                    version_no=src.get("_qmetry_version_no", 1),
                    case_status=case.status,
                    step_results=step_results,
                    mode=mode,
                )
                print(f"pushed {case.id}: exec {r.exec_id}, {r.steps_written} steps, {len(r.errors)} errors")
            except QMetryError as e:
                print(f"error {case.id}: {e}")
```

(The CLI push has no dedicated unit test — it is a thin wrapper over `write_case_execution`, which Task 4 covers. Verify it live in Task 9.)

- [ ] **Step 6: Run the whole suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit** (repo NOT under git — SKIP.)

---

### Task 8: Live-console "Push to QMetry" button (frontend)

**Files:**
- Modify: the live-run view component + its API hook under `frontend/src/` (find the component that renders a finished run and calls `/runs/{id}/report` — the push button lives beside it). Read `FRONTEND.md` first.
- Modify: `frontend/src/` API client (add `pushRunToQmetry(runId)` calling `POST /runs/{id}/push-qmetry`).

**Interfaces:**
- Consumes: `POST /runs/{id}/push-qmetry`.
- Produces: a gated button in the run view.

This task has no backend test. Follow the EXISTING gated-button pattern (the "Log failures to Jira" button and the Manual push button are the models — same disabled logic, same Duke tokens). Do not invent new styling.

- [ ] **Step 1: Add the API call**

In the frontend API client, mirror the existing `POST` helpers:

```js
export async function pushRunToQmetry(runId) {
  const res = await fetch(`/runs/${runId}/push-qmetry`, { method: "POST" });
  if (!res.ok) throw new Error((await res.json()).detail || "push failed");
  return res.json();
}
```

- [ ] **Step 2: Add the gated button**

Next to the existing report/Jira buttons in the run view: a "Push results to QMetry" button, **disabled while `run.status !== "done"`** and while a push is in flight. On click, call `pushRunToQmetry`, then show `pushed/skipped/errors` counts using the existing toast/status pattern. Match the disabled + focus-visible styling of the sibling gated buttons exactly (FRONTEND.md).

- [ ] **Step 3: Build the frontend to verify it compiles**

Run:
```bash
cd frontend
npm run build
```
Expected: build succeeds, no errors.

- [ ] **Step 4: Commit** (repo NOT under git — SKIP.)

---

### Task 9: Full-suite regression + live verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire backend suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (prior 185 tests + the new QMetry/server tests; ~195+).

- [ ] **Step 2: Live read-only sanity — list step executions for a known execution**

Run: `.venv\Scripts\python.exe scripts\qmetry_probe.py step-execs <cycleId> <execId>`
Expected: the step-execution rows print and their count/order matches the case's flattened steps. If not, STOP — the position-mapping assumption is wrong; fix Task 4 (match by id/seqNo) before any live write.

- [ ] **Step 3: Live edit-mode push of ONE case (guarded)**

Pick a disposable test execution. With `QMETRY_EXECUTION_MODE=edit`, run one case and push:
```powershell
$env:QMETRY_EXECUTION_MODE="edit"; .venv\Scripts\python.exe main.py --testcase <id>
```
Then verify in QMetry that each step row shows the expected Pass/Fail/Blocked. (Single-case CLI runs don't auto-push; use the console button or a one-off `--plan` with `--push-qmetry` against a throwaway cycle.)

- [ ] **Step 4: Live create-mode push (guarded)**

With `QMETRY_EXECUTION_MODE=create`, push once and confirm a NEW execution run appears in the same cycle with the step results, leaving the prior run intact. If create writes to the wrong place or the endpoint 404s, STOP and report to Roman (spec decision 2 fallback).

- [ ] **Step 5: Update CLAUDE.md "Current state of play"**

Add a dated bullet noting: per-step QMetry results shipped; `QMETRY_EXECUTION_MODE=edit|create`; gated push from Manual tab, live console, and `main.py --push-qmetry`; test count updated. Update `agent/qmetry.py` module docstring / method list to mention the new methods + helper.

- [ ] **Step 6: Commit** (repo NOT under git — SKIP.)
