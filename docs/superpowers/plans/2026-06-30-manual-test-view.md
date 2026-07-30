# Manual + Agent Test View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second console view where a tester opens a QMetry cycle, marks each case Pass/Fail/Blocked by hand (with a note + flagged failing steps) or runs the AI agent per case, then pushes manual results back to QMetry behind a gate.

**Architecture:** A new server-held "manual session" object (separate from `run_state`) holds per-case marks, snapshotted to disk. New `/manual/*` endpoints on `server.py` build it from the existing `CaseSource` (fixture today, QMetry when keyed), accept marks, run single-case agent runs via the existing orchestrator, and push results via the existing `QMetryClient.post_execution_result`. A new React "Manual" tab renders it, reusing the Duke tokens and `Step.jsx`.

**Tech Stack:** Python 3.14 (async, httpx, FastAPI, dataclasses), pytest + FastAPI TestClient, React 18 + Vite, hand-written CSS from `tokens.css`.

## Global Constraints

- Python invoked only via `.venv\Scripts\python.exe` (Windows venv, Python 3.14).
- **This repo is NOT under git.** Ignore the literal "Commit" pattern — every task ends with a **Checkpoint** step instead: run the full backend suite (`.venv\Scripts\python.exe -m pytest tests/ -q`) and confirm green, or for frontend tasks run `npm run build` and confirm it builds.
- Frontend talks ONLY to `server.py`; no credentials in the browser.
- All UI uses FRONTEND.md tokens (Duke navy `#1B2A6B`, DM Mono for machine text / Inter for human text, desaturated status colors). No CSS framework. Mobile stack at 640px, visible keyboard focus, `prefers-reduced-motion` respected.
- Manual case status values: `unmarked | pass | fail | blocked`. Agent status values: `null | running | pass | fail | blocked`.
- Do NOT change the `run_state` shape. The manual session is a separate contract.
- The QMetry execution id is server-side only — never serialized to the browser.
- `qmetry_configured` is true iff `QMETRY_API_KEY` is set and does not start with `REPLACE_WITH`.
- Sentence case in all copy. Buttons say what happens ("Push results to QMetry", not "Submit").

---

### Task 1: Manual session model + in-memory store with disk persistence

**Files:**
- Create: `agent/manual_state.py`
- Test: `tests/test_manual_state.py`

**Interfaces:**
- Consumes: `agent.run_state.Plan`.
- Produces:
  - `ManualMark(status="unmarked", comment="", failed_steps=[], agent_status=None, agent_run_id=None, pushed_to_qmetry=False)` dataclass with `.to_dict()`.
  - `ManualCase(id, name, steps, mark, execution_id=None)` dataclass with `.to_dict()` (omits `execution_id`).
  - `ManualSession(plan, qmetry_configured, cases)` with `.summary` property, `.to_dict()`, `.find_case(case_id) -> ManualCase`.
  - `ManualStore` with `build(plan_key, plan_name, raw_cases, qmetry_configured) -> ManualSession`, `set_mark(plan_key, case_id, status, comment, failed_steps) -> ManualCase`, `set_agent(plan_key, case_id, agent_status, agent_run_id) -> None`, `mark_pushed(plan_key, case_id) -> None`, `get(plan_key) -> ManualSession | None`.
  - `compose_comment(case: ManualCase) -> str`.
  - `MANUAL_DIR: Path` (default `manual_sessions/` at repo root).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manual_state.py
from __future__ import annotations

import json

import pytest

from agent.manual_state import (
    ManualStore,
    compose_comment,
)

RAW_CASES = [
    {
        "id": "IRHS-R-01",
        "name": "Create inventory recipe",
        "steps": [
            {"action": "Navigate to recipes", "expected": "List loads"},
            {"action": "Click Save", "expected": "Toast appears"},
        ],
        "_qmetry_execution_id": 555,
    },
    {
        "id": "HSHU-01",
        "name": "High-stock hold-unit flow",
        "steps": [{"action": "Flag unit", "expected": "Dialog appears"}],
    },
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    return ManualStore()


def test_build_session_serializes_without_execution_id(store):
    s = store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    d = s.to_dict()
    assert d["plan"] == {"key": "TP-45", "name": "Smoke"}
    assert d["qmetry_configured"] is True
    case0 = d["cases"][0]
    assert case0["id"] == "IRHS-R-01"
    assert case0["manual"]["status"] == "unmarked"
    assert "execution_id" not in case0
    assert "_qmetry_execution_id" not in case0
    assert d["summary"] == {
        "total": 2, "passed": 0, "failed": 0, "blocked": 0, "unmarked": 2, "pushed": 0
    }
    # execution id retained server-side
    assert s.find_case("IRHS-R-01").execution_id == 555
    assert s.find_case("HSHU-01").execution_id is None


def test_set_mark_updates_summary_and_persists(store, tmp_path):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_mark("TP-45", "IRHS-R-01", "fail", "Save did nothing", [1])
    s = store.get("TP-45")
    assert s.summary == {
        "total": 2, "passed": 0, "failed": 1, "blocked": 0, "unmarked": 1, "pushed": 0
    }
    # persisted to disk
    snapshot = json.loads((tmp_path / "TP-45.json").read_text(encoding="utf-8"))
    assert snapshot["IRHS-R-01"]["status"] == "fail"
    assert snapshot["IRHS-R-01"]["failed_steps"] == [1]


def test_marks_survive_rebuild(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_mark("TP-45", "HSHU-01", "pass", "", [])
    # rebuild (e.g. a fresh GET) — marks overlay live cases
    s = store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    assert s.find_case("HSHU-01").mark.status == "pass"


def test_compose_comment_includes_failed_steps(store):
    s = store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_mark("TP-45", "IRHS-R-01", "fail", "Save did nothing", [1])
    case = store.get("TP-45").find_case("IRHS-R-01")
    text = compose_comment(case)
    assert "Save did nothing" in text
    assert "step 2" in text  # 1-based
    assert "Click Save" in text


def test_set_agent_and_mark_pushed(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-abc123")
    store.set_mark("TP-45", "IRHS-R-01", "pass", "", [])
    store.mark_pushed("TP-45", "IRHS-R-01")
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.agent_status == "running"
    assert case.mark.agent_run_id == "run-abc123"
    assert case.mark.pushed_to_qmetry is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.manual_state'`.

- [ ] **Step 3: Write the implementation**

```python
# agent/manual_state.py
"""Manual test-session state — the contract for the Manual console view.

Separate from RunState (which is the *agent* run contract). Holds, per test
case in a cycle, a tester's hand-entered mark (pass/fail/blocked + note +
flagged failing steps) and any per-case agent run that was triggered.

Marks are held in memory keyed by plan, and snapshotted to
`manual_sessions/<plan>.json` so a server restart does not lose them. The cases
and steps themselves always come live from a CaseSource; the stored marks are
overlaid on top each time a session is built.

The QMetry execution id needed to write results back lives on ManualCase but is
NEVER serialized to the browser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.run_state import Plan

MANUAL_DIR = Path(__file__).resolve().parent.parent / "manual_sessions"

ManualStatus = str  # "unmarked" | "pass" | "fail" | "blocked"
AgentStatus = str | None  # None | "running" | "pass" | "fail" | "blocked"


@dataclass
class ManualMark:
    status: ManualStatus = "unmarked"
    comment: str = ""
    failed_steps: list[int] = field(default_factory=list)
    agent_status: AgentStatus = None
    agent_run_id: str | None = None
    pushed_to_qmetry: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "comment": self.comment,
            "failed_steps": list(self.failed_steps),
            "agent_status": self.agent_status,
            "agent_run_id": self.agent_run_id,
            "pushed_to_qmetry": self.pushed_to_qmetry,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ManualMark":
        return cls(
            status=d.get("status", "unmarked"),
            comment=d.get("comment", ""),
            failed_steps=list(d.get("failed_steps", [])),
            agent_status=d.get("agent_status"),
            agent_run_id=d.get("agent_run_id"),
            pushed_to_qmetry=d.get("pushed_to_qmetry", False),
        )


@dataclass
class ManualCase:
    id: str
    name: str
    steps: list[dict]
    mark: ManualMark = field(default_factory=ManualMark)
    execution_id: int | None = None  # server-side only — never serialized

    __test__ = False  # not a pytest class

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "steps": [
                {"action": s.get("action", ""), "expected": s.get("expected", "")}
                for s in self.steps
            ],
            "manual": self.mark.to_dict(),
        }


@dataclass
class ManualSession:
    plan: Plan
    qmetry_configured: bool
    cases: list[ManualCase]

    __test__ = False

    @property
    def summary(self) -> dict:
        def count(status: str) -> int:
            return sum(1 for c in self.cases if c.mark.status == status)

        return {
            "total": len(self.cases),
            "passed": count("pass"),
            "failed": count("fail"),
            "blocked": count("blocked"),
            "unmarked": count("unmarked"),
            "pushed": sum(1 for c in self.cases if c.mark.pushed_to_qmetry),
        }

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.to_dict(),
            "qmetry_configured": self.qmetry_configured,
            "cases": [c.to_dict() for c in self.cases],
            "summary": self.summary,
        }

    def find_case(self, case_id: str) -> ManualCase:
        for c in self.cases:
            if c.id == case_id:
                return c
        raise KeyError(f"Manual case {case_id!r} not in session")


def compose_comment(case: ManualCase) -> str:
    """Build the QMetry comment from the note plus flagged failing steps."""
    mark = case.mark
    lines: list[str] = []
    if mark.comment:
        lines.append(mark.comment)
    if mark.status in ("fail", "blocked") and mark.failed_steps:
        verb = mark.status.capitalize()
        for idx in mark.failed_steps:
            if 0 <= idx < len(case.steps):
                action = case.steps[idx].get("action", "")
                lines.append(f"{verb} at: step {idx + 1} — {action}")
    return "\n".join(lines)


class ManualStore:
    """In-memory marks keyed by plan, snapshotted to disk per plan."""

    def __init__(self) -> None:
        # plan_key -> {case_id -> ManualMark}
        self._marks: dict[str, dict[str, ManualMark]] = {}
        # plan_key -> last built ManualSession (so /mark can return updated case)
        self._sessions: dict[str, ManualSession] = {}

    # ---------------------------------------------------------------- build
    def build(
        self,
        plan_key: str,
        plan_name: str,
        raw_cases: list[dict[str, Any]],
        qmetry_configured: bool,
    ) -> ManualSession:
        marks = self._load_marks(plan_key)
        cases: list[ManualCase] = []
        for rc in raw_cases:
            cid = rc["id"]
            cases.append(
                ManualCase(
                    id=cid,
                    name=rc.get("name", cid),
                    steps=rc.get("steps", []),
                    mark=marks.get(cid, ManualMark()),
                    execution_id=rc.get("_qmetry_execution_id"),
                )
            )
        session = ManualSession(
            plan=Plan(key=plan_key, name=plan_name or plan_key),
            qmetry_configured=qmetry_configured,
            cases=cases,
        )
        self._sessions[plan_key] = session
        return session

    def get(self, plan_key: str) -> ManualSession | None:
        return self._sessions.get(plan_key)

    # ---------------------------------------------------------------- mutate
    def set_mark(
        self,
        plan_key: str,
        case_id: str,
        status: str,
        comment: str,
        failed_steps: list[int],
    ) -> ManualCase:
        case = self._require_case(plan_key, case_id)
        case.mark.status = status
        case.mark.comment = comment
        case.mark.failed_steps = list(failed_steps)
        self._persist(plan_key, case_id, case.mark)
        return case

    def set_agent(
        self, plan_key: str, case_id: str, agent_status: AgentStatus, agent_run_id: str | None
    ) -> None:
        case = self._require_case(plan_key, case_id)
        case.mark.agent_status = agent_status
        if agent_run_id is not None:
            case.mark.agent_run_id = agent_run_id
        self._persist(plan_key, case_id, case.mark)

    def mark_pushed(self, plan_key: str, case_id: str) -> None:
        case = self._require_case(plan_key, case_id)
        case.mark.pushed_to_qmetry = True
        self._persist(plan_key, case_id, case.mark)

    # ---------------------------------------------------------------- internals
    def _require_case(self, plan_key: str, case_id: str) -> ManualCase:
        session = self._sessions.get(plan_key)
        if session is None:
            raise KeyError(f"No manual session for plan {plan_key!r}; GET it first")
        return session.find_case(case_id)

    def _marks_path(self, plan_key: str) -> Path:
        safe = plan_key.replace("/", "_")
        return MANUAL_DIR / f"{safe}.json"

    def _load_marks(self, plan_key: str) -> dict[str, ManualMark]:
        if plan_key in self._marks:
            return self._marks[plan_key]
        path = self._marks_path(plan_key)
        marks: dict[str, ManualMark] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            marks = {cid: ManualMark.from_dict(m) for cid, m in raw.items()}
        self._marks[plan_key] = marks
        return marks

    def _persist(self, plan_key: str, case_id: str, mark: ManualMark) -> None:
        marks = self._marks.setdefault(plan_key, {})
        marks[case_id] = mark
        MANUAL_DIR.mkdir(parents=True, exist_ok=True)
        path = self._marks_path(plan_key)
        path.write_text(
            json.dumps({cid: m.to_dict() for cid, m in marks.items()}, indent=2),
            encoding="utf-8",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all tests pass (86 prior + 5 new = 91).

---

### Task 2: Manual-state fixture + parity test

**Files:**
- Create: `fixtures/sample_manual_state.json`
- Create: `frontend/public/fixtures/sample_manual_state.json` (copy — Vite serves it in dev so the UI works before the backend is up)
- Modify: `tests/test_manual_state.py` (add a parity test)

**Interfaces:**
- Consumes: `ManualStore.build` from Task 1.
- Produces: a canonical fixture the frontend polls when no backend is running.

- [ ] **Step 1: Write the fixture**

```json
// fixtures/sample_manual_state.json
{
  "plan": { "key": "SOUSCLOUD-TP-45", "name": "Inventory · smoke test" },
  "qmetry_configured": false,
  "cases": [
    {
      "id": "IRHS-R-01",
      "name": "Create inventory recipe",
      "steps": [
        { "action": "Navigate to the Inventory > Recipes page", "expected": "The recipe list page loads without error" },
        { "action": "Click the New recipe button", "expected": "The recipe creation form is visible" },
        { "action": "Fill the recipe name with 'Grilled Salmon'", "expected": "The name field accepts the input" },
        { "action": "Click Save", "expected": "A confirmation toast appears and the recipe is in the list" }
      ],
      "manual": { "status": "pass", "comment": "", "failed_steps": [], "agent_status": null, "agent_run_id": null, "pushed_to_qmetry": false }
    },
    {
      "id": "HSHU-01",
      "name": "High-stock hold-unit flow",
      "steps": [
        { "action": "Navigate to the Stock > Hold units page", "expected": "The hold-unit list page loads" },
        { "action": "Flag the first hold-unit for high-stock review", "expected": "A confirmation dialog appears asking to flag the hold-unit" }
      ],
      "manual": { "status": "fail", "comment": "No confirmation dialog appeared after flagging.", "failed_steps": [1], "agent_status": null, "agent_run_id": null, "pushed_to_qmetry": false }
    },
    {
      "id": "MUHC-01",
      "name": "Multi-unit harvest cycle",
      "steps": [
        { "action": "Navigate to the Harvest > Cycles page", "expected": "The harvest cycle list loads" },
        { "action": "Click Start new cycle", "expected": "A new-cycle wizard appears" }
      ],
      "manual": { "status": "unmarked", "comment": "", "failed_steps": [], "agent_status": null, "agent_run_id": null, "pushed_to_qmetry": false }
    }
  ],
  "summary": { "total": 3, "passed": 1, "failed": 1, "blocked": 0, "unmarked": 1, "pushed": 0 }
}
```

- [ ] **Step 2: Copy it to the frontend public dir**

Run: `cp fixtures/sample_manual_state.json frontend/public/fixtures/sample_manual_state.json`
Expected: file exists in both locations, byte-identical.

- [ ] **Step 3: Write the parity test**

```python
# append to tests/test_manual_state.py
import json as _json
from pathlib import Path as _Path

from agent.manual_state import ManualStore as _Store


def test_fixture_matches_built_session_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    fixture = _json.loads(
        (_Path(__file__).resolve().parent.parent / "fixtures" / "sample_manual_state.json")
        .read_text(encoding="utf-8")
    )
    raw_cases = [
        {"id": c["id"], "name": c["name"], "steps": c["steps"]}
        for c in fixture["cases"]
    ]
    built = _Store().build("SOUSCLOUD-TP-45", "Inventory · smoke test", raw_cases, False).to_dict()
    # same top-level keys
    assert set(built.keys()) == set(fixture.keys())
    # same case keys
    assert set(built["cases"][0].keys()) == set(fixture["cases"][0].keys())
    # same manual-mark keys
    assert set(built["cases"][0]["manual"].keys()) == set(fixture["cases"][0]["manual"].keys())
    # same summary keys
    assert set(built["summary"].keys()) == set(fixture["summary"].keys())


def test_frontend_fixture_copy_is_identical():
    root = _Path(__file__).resolve().parent.parent
    a = (root / "fixtures" / "sample_manual_state.json").read_text(encoding="utf-8")
    b = (root / "frontend" / "public" / "fixtures" / "sample_manual_state.json").read_text(encoding="utf-8")
    assert a == b
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_manual_state.py -v`
Expected: PASS (7 tests total).

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all green (93 tests).

---

### Task 3: Shared case-source/QMetry helpers + `GET /manual/{plan}`

**Files:**
- Modify: `server.py` (add `_qmetry_configured`, `_make_case_source`, refactor `_build_orchestrator` to use it, add `MANUAL` store + the GET endpoint)
- Test: `tests/test_server.py` (add manual GET tests)

**Interfaces:**
- Consumes: `agent.manual_state.ManualStore`, `agent.case_source.FixtureCaseSource`, `agent.qmetry.QMetryCaseSource`.
- Produces:
  - module-level `MANUAL = ManualStore()`
  - `_qmetry_configured() -> bool`
  - `_make_case_source() -> CaseSource`
  - `GET /manual/{plan}` returning `ManualSession.to_dict()`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_server.py
from unittest.mock import AsyncMock as _AsyncMock


def _fake_case_source(cases, plan_name="Smoke"):
    cs = _AsyncMock()
    cs.get_plan = _AsyncMock(return_value={"key": "TP-45", "name": plan_name})
    cs.list_cases = _AsyncMock(return_value=cases)
    return cs


def test_get_manual_builds_session(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "go", "expected": "ok"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)

    r = client.get("/manual/TP-45")
    assert r.status_code == 200
    body = r.json()
    assert body["plan"]["key"] == "TP-45"
    assert body["qmetry_configured"] is False
    assert body["cases"][0]["id"] == "A"
    assert body["cases"][0]["manual"]["status"] == "unmarked"
    assert "execution_id" not in body["cases"][0]
    assert body["summary"]["total"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py::test_get_manual_builds_session -v`
Expected: FAIL — 404 (route not defined) or AttributeError on `server_mod.MANUAL`.

- [ ] **Step 3: Implement in `server.py`**

Add imports near the top (with the other `agent.*` imports):

```python
from agent.case_source import CaseSource, FixtureCaseSource
from agent.manual_state import ManualStore, compose_comment
```

Add after the `LATEST` registry definitions:

```python
MANUAL = ManualStore()
```

Add these helpers (place them above `_build_orchestrator`):

```python
def _qmetry_configured() -> bool:
    key = os.environ.get("QMETRY_API_KEY", "")
    return bool(key) and not key.startswith("REPLACE_WITH")


def _make_case_source() -> CaseSource:
    """QMetry when keyed, fixtures otherwise — shared by runs and the manual view."""
    if _qmetry_configured():
        from agent.qmetry import QMetryCaseSource

        return QMetryCaseSource()
    return FixtureCaseSource()
```

Replace the body of `_build_orchestrator` so it reuses `_make_case_source`:

```python
def _build_orchestrator(on_update) -> Orchestrator:
    """Construct the orchestrator with environment-driven defaults."""
    return Orchestrator(case_source=_make_case_source(), on_update=on_update)
```

Add the endpoint (after `get_run`):

```python
@app.get("/manual/{plan}")
async def get_manual(plan: str) -> dict:
    """Build (or rebuild) the manual session for a plan and return its state."""
    source = _make_case_source()
    try:
        meta = await source.get_plan(plan)
        cases = await source.list_cases(plan)
    except Exception as e:
        log.exception("Could not load manual plan %s", plan)
        raise HTTPException(502, f"Could not load plan from source: {e}")
    session = MANUAL.build(plan, meta.get("name", plan), cases, _qmetry_configured())
    return session.to_dict()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py::test_get_manual_builds_session -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all green (94 tests).

---

### Task 4: `POST /manual/{plan}/cases/{id}/mark`

**Files:**
- Modify: `server.py` (add the mark endpoint + a `MarkBody` model)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `MANUAL` store, `get_manual` (session must be built first).
- Produces: `POST /manual/{plan}/cases/{case_id}/mark` body `{status, comment, failed_steps}` → updated case dict.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_server.py
def test_mark_updates_case(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "go", "expected": "ok"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")  # build the session first

    r = client.post(
        "/manual/TP-45/cases/A/mark",
        json={"status": "fail", "comment": "broke", "failed_steps": [0]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["manual"]["status"] == "fail"
    assert body["manual"]["comment"] == "broke"
    assert body["manual"]["failed_steps"] == [0]


def test_mark_rejects_bad_status(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": []}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")
    r = client.post("/manual/TP-45/cases/A/mark", json={"status": "nope"})
    assert r.status_code == 422


def test_mark_unknown_session_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    r = client.post("/manual/NOPE/cases/A/mark", json={"status": "pass"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k mark -v`
Expected: FAIL (404 route not defined).

- [ ] **Step 3: Implement in `server.py`**

Add the request model near `StartRunBody`:

```python
from typing import Literal


class MarkBody(BaseModel):
    status: Literal["unmarked", "pass", "fail", "blocked"]
    comment: str = ""
    failed_steps: list[int] = []
```

Add the endpoint after `get_manual`:

```python
@app.post("/manual/{plan}/cases/{case_id}/mark")
async def mark_case(plan: str, case_id: str, body: MarkBody) -> dict:
    try:
        case = MANUAL.set_mark(plan, case_id, body.status, body.comment, body.failed_steps)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return case.to_dict()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k mark -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all green (97 tests).

---

### Task 5: `POST /manual/{plan}/cases/{id}/run-agent`

**Files:**
- Modify: `server.py` (add the run-agent endpoint + a background wrapper)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `MANUAL` store, `Orchestrator.run_single_case(case_id, plan_key)`, the existing `RUNS`/`_make_on_update` machinery.
- Produces: `POST /manual/{plan}/cases/{case_id}/run-agent` → `{"run_id": ...}`; sets `mark.agent_status="running"` and `mark.agent_run_id` immediately; updates `agent_status` to the case's final status when the run finishes.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_server.py
def test_run_agent_starts_single_case(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "go", "expected": "ok"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")

    # neutralize the background agent run
    with patch.object(server_mod, "_run_agent_case", new=AsyncMock()):
        r = client.post("/manual/TP-45/cases/A/run-agent")
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert run_id.startswith("run-")
    # mark reflects an in-flight agent run
    case = server_mod.MANUAL.get("TP-45").find_case("A")
    assert case.mark.agent_status == "running"
    assert case.mark.agent_run_id == run_id


def test_run_agent_unknown_case_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": []}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")
    r = client.post("/manual/TP-45/cases/NOPE/run-agent")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k run_agent -v`
Expected: FAIL (route not defined / `_run_agent_case` attribute missing).

- [ ] **Step 3: Implement in `server.py`**

Add the background wrapper after `_run_in_background`:

```python
async def _run_agent_case(run_id: str, plan: str, case_id: str, state: RunState) -> None:
    """Run a single case for the manual view; reflect its result on the mark."""
    try:
        orch = _build_orchestrator(_make_on_update(run_id))
        final = await orch.run_single_case(case_id, plan_key=plan)
        RUNS[run_id] = final
        case = next((c for c in final.test_cases if c.id == case_id), None)
        MANUAL.set_agent(plan, case_id, case.status if case else "blocked", run_id)
    except Exception:
        log.exception("Manual agent run %s crashed", run_id)
        state.finish()
        _make_on_update(run_id)(state)
        MANUAL.set_agent(plan, case_id, "blocked", run_id)
```

Add the endpoint after `mark_case`:

```python
@app.post("/manual/{plan}/cases/{case_id}/run-agent")
async def run_agent_for_case(plan: str, case_id: str) -> dict:
    from agent.run_state import new_run_state

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
    MANUAL.set_agent(plan, case_id, "running", state.run_id)

    task = asyncio.create_task(_run_agent_case(state.run_id, plan, case_id, state))
    TASKS[state.run_id] = task
    return {"run_id": state.run_id}
```

Ensure `TestCase` is imported in `server.py` (add to the `agent.run_state` import if absent):

```python
from agent.run_state import RunState, TestCase
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k run_agent -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all green (99 tests).

---

### Task 6: `POST /manual/{plan}/push-qmetry` (gated)

**Files:**
- Modify: `server.py` (add the gated push endpoint)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `MANUAL` store, `compose_comment`, `agent.qmetry.QMetryClient.post_execution_result`.
- Produces: `POST /manual/{plan}/push-qmetry` → `{"pushed": [...], "skipped": [...], "errors": [...]}`; 409 when QMetry not configured or nothing marked.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_server.py
def test_push_qmetry_409_when_not_configured(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": []}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")
    client.post("/manual/TP-45/cases/A/mark", json={"status": "pass"})
    r = client.post("/manual/TP-45/push-qmetry")
    assert r.status_code == 409
    assert "qmetry" in r.json()["detail"].lower()


def test_push_qmetry_409_when_nothing_marked(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [], "_qmetry_execution_id": 9}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    client.get("/manual/TP-45")  # nothing marked yet
    r = client.post("/manual/TP-45/push-qmetry")
    assert r.status_code == 409
    assert "nothing" in r.json()["detail"].lower()


def test_push_qmetry_posts_marked_cases(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [
        {"id": "A", "name": "Case A", "steps": [{"action": "Click Save", "expected": "ok"}],
         "_qmetry_execution_id": 111},
        {"id": "B", "name": "Case B", "steps": [], "_qmetry_execution_id": None},  # no exec id -> skipped
    ]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    client.get("/manual/TP-45")
    client.post("/manual/TP-45/cases/A/mark", json={"status": "fail", "comment": "x", "failed_steps": [0]})
    client.post("/manual/TP-45/cases/B/mark", json={"status": "pass"})

    fake = AsyncMock()
    fake.post_execution_result = AsyncMock(return_value=None)
    fake.aclose = AsyncMock()
    monkeypatch.setattr("agent.qmetry.QMetryClient", lambda **kw: fake)

    r = client.post("/manual/TP-45/push-qmetry")
    assert r.status_code == 200
    body = r.json()
    assert body["pushed"] == ["A"]
    assert "B" in body["skipped"]
    # A got posted with composed comment
    args, kwargs = fake.post_execution_result.call_args
    assert kwargs.get("status", args[2] if len(args) > 2 else None) == "fail"
    case_a = server_mod.MANUAL.get("TP-45").find_case("A")
    assert case_a.mark.pushed_to_qmetry is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k push_qmetry -v`
Expected: FAIL (route not defined).

- [ ] **Step 3: Implement in `server.py`**

Add the endpoint after `run_agent_for_case`:

```python
@app.post("/manual/{plan}/push-qmetry")
async def push_manual_to_qmetry(plan: str) -> dict:
    """Gated: push marked manual results to the QMetry cycle.

    Skips cases that are unmarked or have no QMetry execution id. Per-case
    failures are reported, not fatal.
    """
    if not _qmetry_configured():
        raise HTTPException(409, "QMetry is not configured — set QMETRY_API_KEY first")

    session = MANUAL.get(plan)
    if session is None:
        raise HTTPException(404, f"No manual session for plan {plan!r}; GET it first")

    marked = [c for c in session.cases if c.mark.status != "unmarked"]
    if not marked:
        raise HTTPException(409, "Nothing marked — mark at least one case first")

    from agent.qmetry import QMetryClient, QMetryError

    client = QMetryClient()
    pushed: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []
    try:
        for case in marked:
            if case.execution_id is None:
                skipped.append(case.id)
                continue
            try:
                await client.post_execution_result(
                    cycle_id=plan,
                    execution_id=case.execution_id,
                    status=case.mark.status,
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

Note: `QMetryClient` in Task-1 code uses a per-request `httpx.AsyncClient`, so it may not have an `aclose`; the `getattr` guard above handles both shapes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k push_qmetry -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all green (102 tests).

---

### Task 7: Document the manual contract in FRONTEND.md + CLAUDE.md

**Files:**
- Modify: `FRONTEND.md` (add a "Manual session state" section + the new endpoints + the Manual tab)
- Modify: `CLAUDE.md` (note the new module, endpoints, and view)

**Interfaces:**
- Consumes: the shape from Task 1, the endpoints from Tasks 3–6.
- Produces: documentation only — no code.

- [ ] **Step 1: Add the manual contract to FRONTEND.md**

Insert a new section after the "How the frontend connects to the agent" section:

```markdown
---

## Manual session state (Manual tab)

The console has two tabs: **Manual** and **Live run**. Live run is the execution
tape above. Manual is a hand-testing checklist over the same cycle. It reads a
separate state object from the agent server.

`GET /manual/{plan}` returns:

```json
{
  "plan": { "key": "SOUSCLOUD-TP-45", "name": "Inventory · smoke test" },
  "qmetry_configured": false,
  "cases": [
    {
      "id": "IRHS-R-01",
      "name": "Create inventory recipe",
      "steps": [{ "action": "…", "expected": "…" }],
      "manual": {
        "status": "unmarked",        // unmarked | pass | fail | blocked
        "comment": "",
        "failed_steps": [],           // step indices flagged on fail/blocked
        "agent_status": null,         // null | running | pass | fail | blocked
        "agent_run_id": null,
        "pushed_to_qmetry": false
      }
    }
  ],
  "summary": { "total": 3, "passed": 1, "failed": 1, "blocked": 0, "unmarked": 1, "pushed": 0 }
}
```

The QMetry execution id used to write results back is server-side only and never
appears in this payload.

### Endpoints the Manual tab calls
- `GET  /manual/{plan}` → the state above.
- `POST /manual/{plan}/cases/{id}/mark` body `{status, comment, failed_steps}` → updated case.
- `POST /manual/{plan}/cases/{id}/run-agent` → `{run_id}`; tape subscribes via `GET /runs/{id}`.
- `POST /manual/{plan}/push-qmetry` → `{pushed, skipped, errors}`; gated (409 if QMetry
  not configured or nothing marked). This is the human-in-the-loop write gate, like
  "Log failures to Jira" on the Live tab.

### Marking UX
- Mark per case: Pass / Fail / Blocked + an optional note.
- On Fail/Blocked, flag the step(s) that broke; those ride into the QMetry comment.
- "Push results to QMetry" is disabled during an agent run, when nothing is marked,
  and when `qmetry_configured` is false (shows "Connect QMetry to push results").
```

- [ ] **Step 2: Note the new pieces in CLAUDE.md**

Under "Backend modules — what each does", add:

```markdown
### agent/manual_state.py
The Manual-tab contract. `ManualStore` holds per-case hand marks
(pass/fail/blocked + note + flagged failing steps + any per-case agent run),
keyed by plan and snapshotted to `manual_sessions/<plan>.json`. `ManualSession`
serializes to the shape in FRONTEND.md's "Manual session state". `compose_comment`
builds the QMetry comment from the note + flagged steps. The QMetry execution id is
held server-side only.
```

In the `server.py` endpoint list, add:

```markdown
- `GET /manual/{plan}` → manual session state.
- `POST /manual/{plan}/cases/{id}/mark` → record a hand mark.
- `POST /manual/{plan}/cases/{id}/run-agent` → run one case with the agent.
- `POST /manual/{plan}/push-qmetry` → gated push of manual results to QMetry.
```

- [ ] **Step 3: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: still green (docs-only change; 102 tests). Confirm both files read correctly.

---

### Task 8: Frontend — `useManualState` hook + manual API helpers

**Files:**
- Create: `frontend/src/hooks/useManualState.js`

**Interfaces:**
- Consumes: `GET /manual/{plan}` and the POST endpoints (Tasks 3–6); the fixture at `/fixtures/sample_manual_state.json` when no backend is reachable.
- Produces: `useManualState(planKey)` → `{ state, error, refresh }`; `markCase(plan, caseId, body)`, `runAgentCase(plan, caseId)`, `pushToQmetry(plan)`.

- [ ] **Step 1: Write the hook**

```javascript
// frontend/src/hooks/useManualState.js
import { useCallback, useEffect, useState } from 'react'

// Polls GET /manual/{plan}. When the backend is unreachable (scaffold/dev with
// no server), falls back to the static fixture so the UI still renders.
const FIXTURE_URL = '/fixtures/sample_manual_state.json'

export function useManualState(planKey) {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    const url = planKey ? `/manual/${encodeURIComponent(planKey)}` : FIXTURE_URL
    try {
      let res = await fetch(url, { cache: 'no-store' })
      if (!res.ok && planKey) {
        // backend not up — show the fixture so the page is usable
        res = await fetch(FIXTURE_URL, { cache: 'no-store' })
      }
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      setState(await res.json())
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [planKey])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { state, error, refresh }
}

export async function markCase(planKey, caseId, body) {
  const res = await fetch(
    `/manual/${encodeURIComponent(planKey)}/cases/${encodeURIComponent(caseId)}/mark`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
  if (!res.ok) throw new Error(`Mark failed: ${res.status}`)
  return res.json()
}

export async function runAgentCase(planKey, caseId) {
  const res = await fetch(
    `/manual/${encodeURIComponent(planKey)}/cases/${encodeURIComponent(caseId)}/run-agent`,
    { method: 'POST' },
  )
  if (!res.ok) throw new Error(`Run agent failed: ${res.status}`)
  return res.json()
}

export async function pushToQmetry(planKey) {
  const res = await fetch(`/manual/${encodeURIComponent(planKey)}/push-qmetry`, {
    method: 'POST',
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `Push failed: ${res.status}`)
  }
  return res.json()
}
```

- [ ] **Step 2: Checkpoint**

Run: `cd frontend && npm run build`
Expected: build succeeds (the hook is valid JS; not yet imported anywhere).

---

### Task 9: Frontend — tab toggle in `App.jsx` + `ManualView` shell

**Files:**
- Modify: `frontend/src/App.jsx` (add a `Manual | Live run` tab; default Manual)
- Create: `frontend/src/components/ManualView.jsx`
- Modify: `frontend/src/tokens.css` (add tab + manual layout styles)

**Interfaces:**
- Consumes: `useManualState` (Task 8), `Rail`, `StatStrip`.
- Produces: `ManualView({ plan })` rendering rail-less stage content for manual mode; the App owns the rail and tab switch.

- [ ] **Step 1: Add the tab state + ManualView mount to `App.jsx`**

Replace the `return (...)` block in `frontend/src/App.jsx` so a tab toggle wraps the two views. Keep all existing Live-run logic; add:

```jsx
import ManualView from './components/ManualView.jsx'
// ...existing imports stay...

// inside App(), add near the other useState calls:
const [tab, setTab] = useState('manual') // 'manual' | 'live'
const planKey = state?.plan?.key ?? 'SOUSCLOUD-TP-45'

// Replace the <main className="stage"> ... </main> with the tab-aware shell:
return (
  <div className="app">
    <Rail state={state} activeId={activeId} onSelectCase={setActiveId} />
    <main className="stage">
      <nav className="view-tabs" role="tablist" aria-label="Console view">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'manual'}
          className={`view-tab ${tab === 'manual' ? 'active' : ''}`}
          onClick={() => setTab('manual')}
        >
          Manual
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'live'}
          className={`view-tab ${tab === 'live' ? 'active' : ''}`}
          onClick={() => setTab('live')}
        >
          Live run
        </button>
      </nav>

      {tab === 'live' ? (
        <>
          <header className="stage-head">
            <span className="stage-head-id">{activeCase?.id ?? state?.plan?.key ?? '—'}</span>
            <h1 className="stage-head-title">
              {activeCase?.name ?? state?.plan?.name ?? 'QA Agent Console'}
            </h1>
            <button
              type="button"
              className={`btn btn-primary ${isRunning ? 'running' : ''}`}
              disabled={isRunning || starting}
              onClick={handleRun}
            >
              {runLabel}
            </button>
          </header>
          <StatStrip state={state} />
          <ExecutionTape activeCase={activeCase} />
          <StageFoot
            state={state}
            activeCase={activeCase}
            onReport={handleReport}
            onLogBugs={handleLogBugs}
          />
        </>
      ) : (
        <ManualView plan={planKey} />
      )}

      {error && (
        <div role="alert" className="toast-error">{error}</div>
      )}
    </main>
  </div>
)
```

(Move the existing inline error-toast styles into a `.toast-error` class in `tokens.css` — see Step 3.)

- [ ] **Step 2: Create `ManualView.jsx`**

```jsx
// frontend/src/components/ManualView.jsx
// Manual-mode stage: stat strip + the selected case panel.
// The rail (owned by App) lists cases; here we pick one and mark it.

import { useEffect, useMemo, useState } from 'react'

import ManualCase from './ManualCase.jsx'
import { pushToQmetry, useManualState } from '../hooks/useManualState.js'

export default function ManualView({ plan }) {
  const { state, error, refresh } = useManualState(plan)
  const [activeId, setActiveId] = useState(null)
  const [pushing, setPushing] = useState(false)
  const [pushMsg, setPushMsg] = useState(null)

  useEffect(() => {
    if (!state?.cases?.length) return
    if (activeId && state.cases.find((c) => c.id === activeId)) return
    setActiveId(state.cases[0].id)
  }, [state, activeId])

  const activeCase = useMemo(
    () => state?.cases?.find((c) => c.id === activeId) ?? null,
    [state, activeId],
  )
  const summary = state?.summary ?? { total: 0, passed: 0, failed: 0, blocked: 0, unmarked: 0 }
  const anyMarked = summary.total - summary.unmarked > 0
  const agentRunning = state?.cases?.some((c) => c.manual.agent_status === 'running')
  const pushEnabled = state?.qmetry_configured && anyMarked && !agentRunning && !pushing

  async function handlePush() {
    if (!pushEnabled) return
    setPushing(true)
    setPushMsg(null)
    try {
      const res = await pushToQmetry(plan)
      setPushMsg(`Pushed ${res.pushed.length} · skipped ${res.skipped.length} · errors ${res.errors.length}`)
      await refresh()
    } catch (e) {
      setPushMsg(e.message)
    } finally {
      setPushing(false)
    }
  }

  const pushTitle = !state?.qmetry_configured
    ? 'Connect QMetry to push results'
    : !anyMarked
      ? 'Mark at least one case first'
      : agentRunning
        ? 'Wait for the agent run to finish'
        : 'Push manual results to the QMetry cycle'

  return (
    <div className="manual">
      <div className="manual-cases-nav" role="tablist" aria-label="Test cases">
        {state?.cases?.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`manual-case-chip ${c.id === activeId ? 'active' : ''} ${c.manual.status}`}
            onClick={() => setActiveId(c.id)}
          >
            <span className={`dot ${c.manual.status}`} aria-hidden="true" />
            <span className="chip-id">{c.id}</span>
          </button>
        ))}
      </div>

      <div className="stat-strip">
        <Stat label="Total" value={summary.total} />
        <Stat label="Passed" value={summary.passed} cls="green" />
        <Stat label="Failed" value={summary.failed} cls="red" />
        <Stat label="Blocked" value={summary.blocked} cls="amber" />
        <Stat label="Remaining" value={summary.unmarked} />
      </div>

      {activeCase ? (
        <ManualCase plan={plan} testCase={activeCase} onChanged={refresh} />
      ) : (
        <p className="manual-empty">No cases in this cycle yet.</p>
      )}

      <footer className="stage-foot">
        <span className="status-line">
          <span className={`status-dot ${state?.qmetry_configured ? 'done' : 'idle'}`} />
          {pushMsg ?? (state?.qmetry_configured ? 'Marks save as you go.' : 'QMetry not connected — marks are local.')}
        </span>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!pushEnabled}
          title={pushTitle}
          onClick={handlePush}
        >
          {pushing ? 'Pushing…' : 'Push results to QMetry'}
        </button>
      </footer>

      {error && <div role="alert" className="toast-error">{error}</div>}
    </div>
  )
}

function Stat({ label, value, cls }) {
  return (
    <div className="stat">
      <div className={`stat-num ${cls ?? ''}`}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}
```

- [ ] **Step 3: Add styles to `tokens.css`**

Append (uses only existing token vars):

```css
/* ---- view tabs ---- */
.view-tabs { display: flex; gap: 4px; padding: 10px 20px 0; border-bottom: 1px solid var(--line); }
.view-tab {
  font-family: var(--font); font-size: 13px; font-weight: 500;
  color: var(--muted); background: none; border: none; padding: 8px 14px;
  border-bottom: 2px solid transparent; cursor: pointer;
}
.view-tab.active { color: var(--navy); border-bottom-color: var(--navy); }
.view-tab:focus-visible { outline: 2px solid var(--navy-bright); outline-offset: 2px; }

/* ---- manual view ---- */
.manual { display: flex; flex-direction: column; min-height: 0; flex: 1; }
.manual-cases-nav { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 20px; }
.manual-case-chip {
  display: inline-flex; align-items: center; gap: 6px;
  font-family: var(--mono); font-size: 11px; color: var(--ink);
  background: var(--white); border: 1px solid var(--line); border-radius: 6px;
  padding: 5px 9px; cursor: pointer;
}
.manual-case-chip.active { border-color: var(--navy); background: var(--navy-soft); }
.manual-case-chip:focus-visible { outline: 2px solid var(--navy-bright); outline-offset: 2px; }
.dot { width: 8px; height: 8px; border-radius: 50%; border: 1.5px dashed var(--faint); }
.dot.pass { background: var(--green); border: none; }
.dot.fail { background: var(--red); border: none; }
.dot.blocked { background: var(--amber); border: none; }
.manual-empty { color: var(--muted); padding: 24px 20px; }

.toast-error {
  position: fixed; bottom: 12px; right: 12px; padding: 8px 12px;
  background: var(--red-soft); color: var(--red); border: 1px solid var(--red);
  border-radius: 6px; font-size: 12px; font-family: var(--mono);
}

@media (max-width: 640px) {
  .view-tabs { padding: 8px 12px 0; }
}
```

- [ ] **Step 4: Checkpoint**

Run: `cd frontend && npm run build`
Expected: build fails ONLY because `ManualCase.jsx` doesn't exist yet — that's Task 10. If any OTHER error appears, fix it before moving on. (If you prefer a clean build here, temporarily stub `ManualCase.jsx` with `export default function ManualCase(){return null}` and complete it in Task 10.)

---

### Task 10: Frontend — `ManualCase.jsx` (steps, marking bar, notes, inline agent tape)

**Files:**
- Create: `frontend/src/components/ManualCase.jsx`
- Modify: `frontend/src/tokens.css` (add case-panel styles)

**Interfaces:**
- Consumes: `markCase`, `runAgentCase` (Task 8), `useRunState` (existing), `Step` (existing).
- Produces: `ManualCase({ plan, testCase, onChanged })` — the full per-case panel.

- [ ] **Step 1: Create `ManualCase.jsx`**

```jsx
// frontend/src/components/ManualCase.jsx
// One test case: read-only steps, a per-case marking bar (Pass/Fail/Blocked),
// step-flagging + notes on fail/blocked, and an optional inline agent run.

import { useEffect, useState } from 'react'

import Step from './Step.jsx'
import { markCase, runAgentCase } from '../hooks/useManualState.js'
import { useRunState } from '../hooks/useRunState.js'

const STATUSES = [
  { key: 'pass', label: 'Pass' },
  { key: 'fail', label: 'Fail' },
  { key: 'blocked', label: 'Blocked' },
]

export default function ManualCase({ plan, testCase, onChanged }) {
  const m = testCase.manual
  const [status, setStatus] = useState(m.status === 'unmarked' ? null : m.status)
  const [comment, setComment] = useState(m.comment || '')
  const [failedSteps, setFailedSteps] = useState(m.failed_steps || [])
  const [saving, setSaving] = useState(false)
  const [agentRunId, setAgentRunId] = useState(m.agent_run_id || null)

  // Reset local form when switching cases.
  useEffect(() => {
    setStatus(m.status === 'unmarked' ? null : m.status)
    setComment(m.comment || '')
    setFailedSteps(m.failed_steps || [])
    setAgentRunId(m.agent_run_id || null)
  }, [testCase.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const showFlags = status === 'fail' || status === 'blocked'

  function toggleStep(i) {
    setFailedSteps((prev) => (prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i].sort((a, b) => a - b)))
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
    const { run_id } = await runAgentCase(plan, testCase.id)
    setAgentRunId(run_id)
    await onChanged?.()
  }

  return (
    <section className="manual-case">
      <header className="manual-case-head">
        <span className="stage-head-id">{testCase.id}</span>
        <h2 className="stage-head-title">{testCase.name}</h2>
        <button type="button" className="btn btn-ghost" onClick={handleRunAgent}>
          ▶ Run with agent
        </button>
      </header>

      <ol className="manual-steps">
        {testCase.steps.map((s, i) => (
          <li key={i} className={`manual-step ${showFlags && failedSteps.includes(i) ? 'flagged ' + status : ''}`}>
            <span className="manual-step-no">{i + 1}</span>
            <div className="manual-step-body">
              <div className="manual-step-action">{s.action}</div>
              {s.expected && <div className="manual-step-expected">▸ {s.expected}</div>}
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

      {agentRunId && <AgentTape runId={agentRunId} caseId={testCase.id} />}
    </section>
  )
}

function AgentTape({ runId, caseId }) {
  const { state } = useRunState(runId)
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

- [ ] **Step 2: Add case-panel styles to `tokens.css`**

```css
/* ---- manual case panel ---- */
.manual-case { padding: 0 20px 20px; overflow-y: auto; flex: 1; min-height: 0; }
.manual-case-head { display: flex; align-items: center; gap: 12px; padding: 8px 0 14px; }
.manual-case-head .stage-head-title { margin: 0; flex: 1; }
.btn-ghost {
  font-family: var(--font); font-size: 13px; color: var(--navy);
  background: var(--white); border: 1px solid var(--navy-line); border-radius: 6px;
  padding: 7px 12px; cursor: pointer;
}
.btn-ghost:hover { background: var(--navy-soft); }

.manual-steps { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.manual-step {
  display: flex; align-items: flex-start; gap: 10px;
  background: var(--white); border: 1px solid var(--line); border-left: 3px solid var(--line);
  border-radius: 6px; padding: 10px 12px;
}
.manual-step.flagged.fail { border-left-color: var(--red); background: var(--red-soft); }
.manual-step.flagged.blocked { border-left-color: var(--amber); background: var(--amber-soft); }
.manual-step-no { font-family: var(--mono); font-size: 12px; color: var(--faint); min-width: 16px; }
.manual-step-body { flex: 1; }
.manual-step-action { font-family: var(--font); font-size: 14px; color: var(--ink); }
.manual-step-expected { font-family: var(--font); font-size: 12px; color: var(--muted); margin-top: 2px; }
.manual-step-flag { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); }

.manual-mark-bar { display: flex; gap: 8px; margin: 16px 0 0; }
.mark-btn {
  font-family: var(--font); font-size: 13px; font-weight: 500; cursor: pointer;
  padding: 8px 18px; border-radius: 6px; border: 1px solid var(--line); background: var(--white); color: var(--ink);
}
.mark-btn.pass.active { background: var(--green-soft); border-color: var(--green); color: var(--green); }
.mark-btn.fail.active { background: var(--red-soft); border-color: var(--red); color: var(--red); }
.mark-btn.blocked.active { background: var(--amber-soft); border-color: var(--amber); color: var(--amber); }
.mark-btn:focus-visible { outline: 2px solid var(--navy-bright); outline-offset: 2px; }
.mark-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.manual-notes { margin-top: 14px; display: flex; flex-direction: column; gap: 4px; }
.manual-notes label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); }
.manual-notes textarea {
  font-family: var(--font); font-size: 13px; color: var(--ink);
  border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; min-height: 64px; resize: vertical;
}
.manual-notes textarea:focus-visible { outline: 2px solid var(--navy-bright); outline-offset: 1px; }

.manual-agent-tape { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 8px; }
```

- [ ] **Step 3: Checkpoint (build)**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual verification (no backend)**

Run: `cd frontend && npm run dev`, open http://localhost:5173. Expected:
- The **Manual** tab is selected by default; case chips render from the fixture.
- IRHS-R-01 shows green dot; HSHU-01 shows red dot with step 2 flagged red.
- Clicking Fail on a case reveals per-step checkboxes + a notes box.
- "Push results to QMetry" is **disabled** with title "Connect QMetry to push results" (fixture has `qmetry_configured: false`).
- Switching to **Live run** shows the unchanged execution tape.
- Narrow the window < 640px: the layout stacks without horizontal scroll.

- [ ] **Step 5: Full verification (with backend, fixture mode)**

In one terminal: `.venv\Scripts\python.exe server.py`. In another: `cd frontend && npm run dev`.
- Manual tab loads via `GET /manual/SOUSCLOUD-TP-45` (check the Network tab).
- Mark a case Fail, flag a step, type a note, blur → `POST …/mark` returns 200; reload preserves the mark (persisted to `manual_sessions/SOUSCLOUD-TP-45.json`).
- Click "Run with agent" on a case → a `run_id` returns and the inline tape appears (steps may BLOCK without Azure creds — that's expected; the point is the wiring).

- [ ] **Step 6: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all backend tests green (102). Frontend has no automated tests; Steps 4–5 are the verification.

---

## Self-Review

**Spec coverage:**
- Both manual + agent per case → Tasks 4 (mark), 5 (run-agent), 10 (UI for both). ✓
- Per-case mark + note + flagged steps → Tasks 1 (`ManualMark`), 4, 10. ✓
- Local results, gated QMetry push → Tasks 1 (store + disk), 6 (gated push), 9 (gate UI). ✓
- New Manual tab beside Live run → Task 9. ✓
- Server-side-only execution id → Task 1 (`to_dict` omits it), asserted in Tasks 1 & 3. ✓
- Auto fixture/QMetry swap + `qmetry_configured` → Task 3 helpers. ✓
- Contract documented in FRONTEND.md + CLAUDE.md → Task 7. ✓
- Fixture + parity test → Task 2. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step has real assertions. ✓

**Type consistency:** `ManualMark`/`ManualCase`/`ManualSession`/`ManualStore` method names (`build`, `set_mark`, `set_agent`, `mark_pushed`, `get`, `find_case`) are used identically across Tasks 1, 3–6. `compose_comment(case)` signature consistent. Endpoint paths consistent between server tasks, FRONTEND.md (Task 7), and the frontend hook (Task 8). `manual` payload keys match between Task 1 `to_dict`, Task 2 fixture, and Task 10 UI reads (`status`, `comment`, `failed_steps`, `agent_status`, `agent_run_id`, `pushed_to_qmetry`). ✓
