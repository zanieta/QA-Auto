"""Server endpoint tests using FastAPI's TestClient.

Verifies the HTTP contract from FRONTEND.md (return codes + gates).
The orchestrator's background task is mocked so we don't need real Azure creds.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import server as server_mod
from agent.run_state import Plan, RunState, TestCase, new_run_state


@pytest.fixture(autouse=True)
def _clear_registries():
    """Reset module-level state between tests."""
    server_mod.RUNS.clear()
    server_mod.TASKS.clear()
    server_mod.LISTENERS.clear()
    server_mod.LATEST.clear()
    server_mod.RUN_CREDENTIALS.clear()
    yield
    server_mod.RUNS.clear()
    server_mod.TASKS.clear()
    server_mod.LISTENERS.clear()
    server_mod.LATEST.clear()
    server_mod.RUN_CREDENTIALS.clear()


@pytest.fixture
def client():
    return TestClient(server_mod.app)


# ----- POST /runs ---------------------------------------------------------


def test_post_runs_returns_run_id(client):
    # neutralize the background task so it doesn't fail on missing Azure creds
    with patch.object(server_mod, "_run_in_background", new=AsyncMock()):
        r = client.post("/runs", json={"plan": "SOUSCLOUD-TP-45"})
    assert r.status_code == 200
    body = r.json()
    assert "run_id" in body and body["run_id"].startswith("run-")
    # state was registered
    assert body["run_id"] in server_mod.RUNS


def test_post_runs_rejects_missing_plan(client):
    r = client.post("/runs", json={})
    assert r.status_code == 422  # FastAPI request-body validation


# ----- GET /runs/{id} -----------------------------------------------------


def test_get_run_returns_state(client):
    state = new_run_state("X", "Plan X")
    server_mod.RUNS[state.run_id] = state
    r = client.get(f"/runs/{state.run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == state.run_id
    assert set(body.keys()) == {
        "run_id", "plan", "status", "elapsed_seconds", "summary", "test_cases"
    }


def test_get_run_unknown_404(client):
    r = client.get("/runs/does-not-exist")
    assert r.status_code == 404


def test_get_run_prefers_live_latest_snapshot(client):
    # RUNS holds the stale initial idle object; LATEST holds the live snapshot.
    state = new_run_state("CY-1", "Cycle 1")  # status idle, 0 cases
    server_mod.RUNS[state.run_id] = state
    server_mod.LATEST[state.run_id] = {
        "run_id": state.run_id,
        "plan": {"key": "CY-1", "name": "Cycle 1"},
        "status": "running",
        "elapsed_seconds": 1.0,
        "summary": {"total": 2, "passed": 1, "failed": 0, "blocked": 0},
        "test_cases": [
            {"id": "A", "name": "A", "status": "pass", "steps": []},
            {"id": "B", "name": "B", "status": "running", "steps": []},
        ],
    }
    r = client.get(f"/runs/{state.run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"          # live, not the idle RUNS object
    assert body["summary"]["total"] == 2
    assert [c["status"] for c in body["test_cases"]] == ["pass", "running"]


# ----- POST /runs/{id}/report --------------------------------------------


def test_report_409_when_not_done(client):
    state = new_run_state("X")
    state.start_run()  # status = running
    server_mod.RUNS[state.run_id] = state
    r = client.post(f"/runs/{state.run_id}/report")
    assert r.status_code == 409


def test_report_writes_html_and_returns_path(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.reporter.REPORTS_DIR", tmp_path)
    state = new_run_state("X")
    state.start_run()
    state.finish()
    server_mod.RUNS[state.run_id] = state
    r = client.post(f"/runs/{state.run_id}/report")
    assert r.status_code == 200
    path = Path(r.json()["path"])
    assert path.exists()
    assert path.suffix == ".html"


# ----- POST /runs/{id}/log-bugs ------------------------------------------


def test_log_bugs_409_when_not_done(client):
    state = new_run_state("X")
    state.start_run()
    server_mod.RUNS[state.run_id] = state
    r = client.post(f"/runs/{state.run_id}/log-bugs")
    assert r.status_code == 409


def test_log_bugs_409_when_no_failures(client):
    state = new_run_state("X")
    state.add_case(TestCase(id="A", name="a", status="pass"))
    state.start_run()
    state.finish()
    server_mod.RUNS[state.run_id] = state
    r = client.post(f"/runs/{state.run_id}/log-bugs")
    assert r.status_code == 409
    assert "no failures" in r.json()["detail"].lower()


def test_log_bugs_creates_bugs_for_each_failure(client, monkeypatch):
    state = new_run_state("X")
    state.add_case(TestCase(id="A", name="a", status="fail"))
    state.add_case(TestCase(id="B", name="b", status="pass"))
    state.start_run()
    state.finish()
    server_mod.RUNS[state.run_id] = state

    # Patch JiraClient where server.py imports it
    fake = AsyncMock()
    fake.create_bug = AsyncMock(return_value={"key": "SOUSCLOUD-99"})
    fake.aclose = AsyncMock()
    monkeypatch.setattr("agent.jira_client.JiraClient", lambda **kw: fake)

    r = client.post(f"/runs/{state.run_id}/log-bugs")
    assert r.status_code == 200
    body = r.json()
    # only the failed case yielded a bug
    assert len(body["created"]) == 1
    assert body["created"][0]["key"] == "SOUSCLOUD-99"
    assert body["errors"] == []


# ----- POST /runs/{id}/cancel ---------------------------------------------


def test_cancel_unknown_run_404(client):
    r = client.post("/runs/does-not-exist/cancel")
    assert r.status_code == 404


def test_cancel_already_finished_run_404(client):
    import asyncio

    async def _mk_done_task():
        async def _noop():
            return None

        t = asyncio.create_task(_noop())
        await t
        return t

    server_mod.TASKS["finished-run"] = asyncio.run(_mk_done_task())
    r = client.post("/runs/finished-run/cancel")
    assert r.status_code == 404


def test_cancel_running_agent_case_marks_cancelled(monkeypatch, tmp_path):
    """Cancel a live manual agent-run task: the endpoint returns
    {"cancelled": True}, the underlying task ends CancelledError, and the
    mark reflects agent_status=None with a "cancelled by tester" note.

    This drives the endpoint function and the background task on the same
    event loop via a single asyncio.run() rather than through the sync
    TestClient — the TestClient spins up its own short-lived event loop per
    call, which can't coordinate with a task created on a different loop.
    """
    import asyncio

    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "do the thing", "expected": "e"}]}]
    server_mod.MANUAL.build("TP-45", "TP-45", cases, False)

    class SlowOrch:
        async def run_single_case(self, case_id, plan_key=None, step_indices=None, credentials=None):
            await asyncio.sleep(100)

    monkeypatch.setattr(server_mod, "_build_orchestrator", lambda cb: SlowOrch())

    async def _scenario():
        state = new_run_state("TP-45", "TP-45")
        state.add_case(TestCase(id="A", name="Case A"))
        server_mod.RUNS[state.run_id] = state
        task = asyncio.create_task(
            server_mod._run_agent_case(state.run_id, "TP-45", "A", state, None)
        )
        server_mod.TASKS[state.run_id] = task
        await asyncio.sleep(0)  # let the task start and reach the sleep

        result = await server_mod.cancel_run(state.run_id)
        assert result == {"cancelled": True}

        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

        mark = server_mod.MANUAL.get("TP-45").find_case("A").mark
        assert mark.agent_status is None
        assert "cancelled by tester" in mark.agent_note

    asyncio.run(_scenario())


# ----- 404 paths ---------------------------------------------------------


def test_report_unknown_run_id_404(client):
    assert client.post("/runs/x/report").status_code == 404


def test_log_bugs_unknown_run_id_404(client):
    assert client.post("/runs/x/log-bugs").status_code == 404


def test_stream_unknown_run_id_404(client):
    assert client.get("/runs/x/stream").status_code == 404


# ----- GET /manual/{plan} -----------------------------------------------

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
    assert body["standalone"] is False


def test_get_manual_unsticks_a_stranded_running_case(client, tmp_path, monkeypatch):
    """`agent_status: "running"` is persisted but run state is in memory, so a
    kill or restart strands the case as running forever — which disables both
    Run and Push for it."""
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": []}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")
    server_mod.MANUAL.set_agent("TP-45", "A", "running", "run-gone")
    server_mod.RUNS.pop("run-gone", None)

    body = client.get("/manual/TP-45").json()
    mark = body["cases"][0]["manual"]
    assert mark["agent_status"] is None
    assert "interrupted" in mark["agent_note"]


def test_get_manual_leaves_a_live_run_alone(client, tmp_path, monkeypatch):
    """A run this process still owns is genuinely running — don't clear it."""
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": []}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")
    server_mod.MANUAL.set_agent("TP-45", "A", "running", "run-live")
    server_mod.RUNS["run-live"] = object()
    try:
        body = client.get("/manual/TP-45").json()
        assert body["cases"][0]["manual"]["agent_status"] == "running"
    finally:
        server_mod.RUNS.pop("run-live", None)


def test_get_manual_asks_the_source_to_skip_steps(client, tmp_path, monkeypatch):
    """Opening a run must cost one call, not one per case — the console fetches
    the steps of the case the tester opens."""
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    source = _fake_case_source([{"id": "A", "name": "Case A", "steps": []}])
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: source)
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)

    client.get("/manual/TP-45")
    source.list_cases.assert_awaited_once_with("TP-45", with_steps=False)


# ----- GET /manual/{plan}/cases/{id}/steps --------------------------------


def test_get_case_steps_hydrates_on_demand(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [
        {"id": "A", "name": "Case A", "steps": [], "_steps_loaded": False},
        {"id": "B", "name": "Case B", "steps": [], "_steps_loaded": False},
    ]
    source = _fake_case_source(cases)
    source.get_case_steps = _AsyncMock(return_value=[{"action": "go", "expected": "ok"}])
    source.get_case_test_data = _AsyncMock(
        return_value=[{"name": "User Role", "value": "Admin"}]
    )
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: source)
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)

    built = client.get("/manual/TP-45").json()
    assert [c["steps_loaded"] for c in built["cases"]] == [False, False]

    r = client.get("/manual/TP-45/cases/A/steps")
    assert r.status_code == 200
    body = r.json()
    assert body["steps"] == [{"action": "go", "expected": "ok", "test_data": ""}]
    assert body["steps_loaded"] is True
    # The case's own test data (QMetry's parameter table) rides along.
    assert body["test_data"] == [{"name": "User Role", "value": "Admin"}]
    source.get_case_steps.assert_awaited_once_with("TP-45", "A")

    # Only the opened case is hydrated; the session keeps the other one deferred.
    after = client.get("/manual/TP-45").json()
    assert {c["id"]: c["steps_loaded"] for c in after["cases"]} == {"A": True, "B": False}


def test_get_case_steps_is_cheap_when_already_loaded(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "go", "expected": "ok"}]}]
    source = _fake_case_source(cases)
    source.get_case_steps = _AsyncMock(return_value=[])
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: source)
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")

    r = client.get("/manual/TP-45/cases/A/steps")
    assert r.status_code == 200
    assert r.json()["steps"] == [{"action": "go", "expected": "ok", "test_data": ""}]
    source.get_case_steps.assert_not_awaited()


def test_get_case_steps_404s_without_a_session(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    r = client.get("/manual/NOPE/cases/A/steps")
    assert r.status_code == 404


def test_get_case_steps_404s_for_unknown_case(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": []}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")
    assert client.get("/manual/TP-45/cases/ZZZ/steps").status_code == 404


def test_push_rejects_a_standalone_case(client, tmp_path, monkeypatch):
    """A library case has no execution — say so instead of silently skipping."""
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    r = client.post("/manual/TC:SOUSCLOUD-TC-2/push-qmetry")
    assert r.status_code == 409
    assert "standalone" in r.json()["detail"].lower()


# ----- POST /manual/{plan}/cases/{id}/mark --------------------------------


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


# ----- POST /manual/{plan}/cases/{id}/steps/{index}/mark -----------------


def _build_session_with_one_step(client, monkeypatch, tmp_path, action="Click Save", expected="Saved"):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": action, "expected": expected}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")


def test_mark_step_happy_path_no_override(client, tmp_path, monkeypatch):
    _build_session_with_one_step(client, monkeypatch, tmp_path)

    r = client.post(
        "/manual/TP-45/cases/A/steps/0/mark",
        json={"status": "pass", "agent_status": "pass"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["manual"]["step_marks"]["0"]["status"] == "pass"
    assert body["manual"]["step_marks"]["0"]["overrode"] is False
    # derived case status reflects the single step mark
    assert body["manual"]["status"] == "pass"


def test_mark_step_override_without_note_422(client, tmp_path, monkeypatch):
    _build_session_with_one_step(client, monkeypatch, tmp_path)

    r = client.post(
        "/manual/TP-45/cases/A/steps/0/mark",
        json={"status": "pass", "agent_status": "blocked"},
    )
    assert r.status_code == 422
    assert "note" in r.json()["detail"].lower()


def test_mark_step_invalid_status_422(client, tmp_path, monkeypatch):
    _build_session_with_one_step(client, monkeypatch, tmp_path)

    r = client.post(
        "/manual/TP-45/cases/A/steps/0/mark",
        json={"status": "nope"},
    )
    assert r.status_code == 422


def test_mark_step_unknown_case_404(client, tmp_path, monkeypatch):
    _build_session_with_one_step(client, monkeypatch, tmp_path)

    r = client.post(
        "/manual/TP-45/cases/NOPE/steps/0/mark",
        json={"status": "pass"},
    )
    assert r.status_code == 404


def test_mark_step_out_of_range_404(client, tmp_path, monkeypatch):
    _build_session_with_one_step(client, monkeypatch, tmp_path)

    r = client.post(
        "/manual/TP-45/cases/A/steps/5/mark",
        json={"status": "pass"},
    )
    assert r.status_code == 404


def test_mark_step_unknown_session_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    r = client.post(
        "/manual/NOPE/cases/A/steps/0/mark",
        json={"status": "pass"},
    )
    assert r.status_code == 404


def test_mark_step_override_writes_knowledge_file(client, tmp_path, monkeypatch):
    _build_session_with_one_step(
        client, monkeypatch, tmp_path, action="Click Save", expected="Record is saved"
    )
    knowledge_file = tmp_path / "eval_overrides.jsonl"
    monkeypatch.setattr("agent.knowledge.KNOWLEDGE_PATH", knowledge_file)

    r = client.post(
        "/manual/TP-45/cases/A/steps/0/mark",
        json={
            "status": "pass",
            "note": "actually saved fine, evaluator misread the toast",
            "agent_status": "blocked",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["manual"]["step_marks"]["0"]["overrode"] is True

    assert knowledge_file.exists()
    lines = knowledge_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    import json as _json

    entry = _json.loads(lines[0])
    assert entry["case_id"] == "A"
    assert entry["step_index"] == 0
    assert entry["step_text"] == "Click Save"
    assert entry["expected"] == "Record is saved"
    assert entry["agent_status"] == "blocked"
    assert entry["human_status"] == "pass"
    assert entry["note"] == "actually saved fine, evaluator misread the toast"


def test_mark_step_non_override_does_not_write_knowledge(client, tmp_path, monkeypatch):
    _build_session_with_one_step(client, monkeypatch, tmp_path)
    knowledge_file = tmp_path / "eval_overrides.jsonl"
    monkeypatch.setattr("agent.knowledge.KNOWLEDGE_PATH", knowledge_file)

    r = client.post(
        "/manual/TP-45/cases/A/steps/0/mark",
        json={"status": "pass", "agent_status": "pass"},
    )
    assert r.status_code == 200
    assert not knowledge_file.exists()


# ----- POST /manual/{plan}/cases/{id}/run-agent ---------------------------


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


def test_run_agent_completion_writes_agent_note(client, tmp_path, monkeypatch):
    """When a manual agent run finishes, its summary lands on mark.agent_note."""
    import asyncio

    from agent.run_state import Step, TestCase, new_run_state

    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "do the thing", "expected": "e"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")

    final = new_run_state("TP-45", "TP-45")
    final.add_case(TestCase(id="A", name="Case A"))
    final.start_case("A")
    final.add_step("A", Step(action="do the thing", detail=""))
    final.resolve_step("A", 0, "pass", "Looks right.", 1.0)
    final.resolve_case("A", "pass")

    class FakeOrch:
        async def run_single_case(self, case_id, plan_key=None, step_indices=None, credentials=None):
            return final

    monkeypatch.setattr(server_mod, "_build_orchestrator", lambda cb: FakeOrch())
    state = new_run_state("TP-45", "TP-45")
    asyncio.run(server_mod._run_agent_case(state.run_id, "TP-45", "A", state, [2]))

    mark = server_mod.MANUAL.get("TP-45").find_case("A").mark
    assert mark.agent_note.splitlines()[0].startswith("Agent run ")
    assert state.run_id in mark.agent_note  # header carries the run id
    assert "steps 3: pass" in mark.agent_note.splitlines()[0]
    assert "Step 3: pass — Looks right." in mark.agent_note


# ----- POST /manual/{plan}/cases/{id}/credentials -------------------------


def test_credentials_endpoint_sets_and_never_echoes(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "go", "expected": "ok"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")  # build the session first

    r = client.post(
        "/manual/TP-45/cases/A/credentials",
        json={"username": "u@x.com", "password": "pw"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["manual"]["login_username"] == "u@x.com"
    assert body["manual"]["has_password"] is True
    assert "pw" not in r.text and "login_password" not in r.text
    # and GET /manual/TP-45 must not leak it either
    r2 = client.get("/manual/TP-45")
    assert "pw" not in r2.text and "login_password" not in r2.text


def test_credentials_endpoint_unknown_case_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": []}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")

    r = client.post(
        "/manual/TP-45/cases/NOPE/credentials",
        json={"username": "u", "password": "p"},
    )
    assert r.status_code == 404


def test_run_agent_case_passes_credentials(client, tmp_path, monkeypatch):
    """_run_agent_case forwards (username, password) to the orchestrator only
    when both are non-empty on the case's mark; otherwise None."""
    import asyncio

    from agent.run_state import Step, TestCase, new_run_state

    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "do the thing", "expected": "e"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")

    client.post(
        "/manual/TP-45/cases/A/credentials",
        json={"username": "u@x.com", "password": "pw"},
    )

    final = new_run_state("TP-45", "TP-45")
    final.add_case(TestCase(id="A", name="Case A"))
    final.start_case("A")
    final.add_step("A", Step(action="do the thing", detail=""))
    final.resolve_step("A", 0, "pass", "Looks right.", 1.0)
    final.resolve_case("A", "pass")

    captured: dict = {}

    class FakeOrch:
        async def run_single_case(self, case_id, plan_key=None, step_indices=None, credentials=None):
            captured["credentials"] = credentials
            return final

    monkeypatch.setattr(server_mod, "_build_orchestrator", lambda cb: FakeOrch())
    state = new_run_state("TP-45", "TP-45")
    asyncio.run(server_mod._run_agent_case(state.run_id, "TP-45", "A", state, None))

    assert captured["credentials"] == ("u@x.com", "pw")

    # clearing credentials (both empty) means no credentials are passed
    client.post(
        "/manual/TP-45/cases/A/credentials",
        json={"username": "", "password": ""},
    )
    captured.clear()
    state2 = new_run_state("TP-45", "TP-45")
    asyncio.run(server_mod._run_agent_case(state2.run_id, "TP-45", "A", state2, None))
    assert captured["credentials"] is None


def test_run_agent_crash_writes_agent_note(client, tmp_path, monkeypatch):
    """When the background agent run crashes, the mark still gets a note."""
    import asyncio

    from agent.run_state import new_run_state

    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [{"id": "A", "name": "Case A", "steps": [{"action": "do the thing", "expected": "e"}]}]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    client.get("/manual/TP-45")

    class CrashingOrch:
        async def run_single_case(self, case_id, plan_key=None, step_indices=None, credentials=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(server_mod, "_build_orchestrator", lambda cb: CrashingOrch())
    state = new_run_state("TP-45", "TP-45")
    state.add_case(TestCase(id="A", name="Case A"))
    asyncio.run(server_mod._run_agent_case(state.run_id, "TP-45", "A", state, None))

    mark = server_mod.MANUAL.get("TP-45").find_case("A").mark
    assert mark.agent_status == "blocked"
    assert mark.agent_note.startswith("Agent run ")
    assert state.run_id in mark.agent_note
    assert "crashed" in mark.agent_note.lower()


# ----- POST /manual/{plan}/push-qmetry ------------------------------------


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
    monkeypatch.setattr(server_mod, "_qmetry_execution_mode", lambda: "edit")
    client.get("/manual/TP-45")
    client.post("/manual/TP-45/cases/A/mark", json={"status": "fail", "comment": "x", "failed_steps": [0]})
    client.post(
        "/manual/TP-45/cases/A/steps/0/mark",
        json={"status": "fail", "note": "broke on save"},
    )
    client.post("/manual/TP-45/cases/B/mark", json={"status": "pass"})

    called = {}

    async def _writer(clientobj, **kwargs):
        called.update(kwargs)
        from agent.qmetry import WriteResult
        return WriteResult(exec_id=kwargs["execution_id"], steps_written=len(kwargs["step_results"]), errors=[])

    monkeypatch.setattr("agent.qmetry.write_case_execution", _writer)

    fake = AsyncMock()
    fake.aclose = AsyncMock()
    monkeypatch.setattr("agent.qmetry.QMetryClient", lambda **kw: fake)

    r = client.post("/manual/TP-45/push-qmetry")
    assert r.status_code == 200
    body = r.json()
    assert body["pushed"] == ["A"]
    assert "B" in body["skipped"]
    # A's write was recorded with the fail status
    assert called["case_status"] == "fail"
    # The composed comment for case A: case-level note ("x") plus one line per
    # marked step ("Step 1: fail — broke on save"), per compose_comment's
    # per-step-line format (docs/superpowers/specs/2026-07-09-step-marks...).
    comment_kwarg = called.get("comment")
    assert comment_kwarg is not None, "comment kwarg must be passed"
    assert "x" in comment_kwarg
    assert "Step 1: fail — broke on save" in comment_kwarg
    case_a = server_mod.MANUAL.get("TP-45").find_case("A")
    assert case_a.mark.pushed_to_qmetry is True


def test_push_qmetry_per_case_error_is_non_fatal(client, tmp_path, monkeypatch):
    """A QMetryError on one case is recorded in errors but the batch continues."""
    from agent.qmetry import QMetryError

    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [
        {"id": "A", "name": "Case A", "steps": [{"action": "Click Save", "expected": "ok"}],
         "_qmetry_execution_id": 111},
        {"id": "B", "name": "Case B", "steps": [], "_qmetry_execution_id": 222},
    ]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    monkeypatch.setattr(server_mod, "_qmetry_execution_mode", lambda: "edit")
    client.get("/manual/TP-45")
    client.post("/manual/TP-45/cases/A/mark", json={"status": "fail", "comment": "broke", "failed_steps": [0]})
    client.post("/manual/TP-45/cases/B/mark", json={"status": "pass"})

    async def _writer(clientobj, **kwargs):
        if kwargs.get("execution_id") == 111:
            from agent.qmetry import QMetryError as _QE
            raise _QE("boom")
        from agent.qmetry import WriteResult
        return WriteResult(exec_id=kwargs["execution_id"], steps_written=len(kwargs["step_results"]), errors=[])

    monkeypatch.setattr("agent.qmetry.write_case_execution", _writer)

    fake = AsyncMock()
    fake.aclose = AsyncMock()
    monkeypatch.setattr("agent.qmetry.QMetryClient", lambda **kw: fake)

    r = client.post("/manual/TP-45/push-qmetry")
    assert r.status_code == 200
    body = r.json()

    # B succeeded; A failed
    assert body["pushed"] == ["B"]
    assert len(body["errors"]) == 1
    assert body["errors"][0]["case"] == "A"

    # A's push failure must NOT be recorded as pushed; B must be recorded as pushed
    session = server_mod.MANUAL.get("TP-45")
    assert session.find_case("A").mark.pushed_to_qmetry is False
    assert session.find_case("B").mark.pushed_to_qmetry is True


def test_push_qmetry_uses_internal_cycle_id(client, tmp_path, monkeypatch):
    """When cases carry _qmetry_cycle_id, push-qmetry must pass the internal id
    as cycle_id â€” NOT the URL path param (the plan key)."""
    monkeypatch.setattr("agent.manual_state.MANUAL_DIR", tmp_path)
    server_mod.MANUAL = server_mod.ManualStore()
    cases = [
        {
            "id": "A",
            "name": "Case A",
            "steps": [{"action": "Click Save", "expected": "ok"}],
            "_qmetry_execution_id": 111,
            "_qmetry_cycle_id": "CYC-INTERNAL-1",
        },
    ]
    monkeypatch.setattr(server_mod, "_make_case_source", lambda: _fake_case_source(cases))
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    monkeypatch.setattr(server_mod, "_qmetry_execution_mode", lambda: "edit")
    client.get("/manual/TP-45")
    client.post("/manual/TP-45/cases/A/mark", json={"status": "pass"})

    called = {}

    async def _writer(clientobj, **kwargs):
        called.update(kwargs)
        from agent.qmetry import WriteResult
        return WriteResult(exec_id=kwargs["execution_id"], steps_written=len(kwargs["step_results"]), errors=[])

    monkeypatch.setattr("agent.qmetry.write_case_execution", _writer)

    fake = AsyncMock()
    fake.aclose = AsyncMock()
    monkeypatch.setattr("agent.qmetry.QMetryClient", lambda **kw: fake)

    r = client.post("/manual/TP-45/push-qmetry")
    assert r.status_code == 200
    body = r.json()
    assert body["pushed"] == ["A"]

    # The critical assertion: cycle_id must be the internal id, NOT "TP-45"
    assert called.get("cycle_id") == "CYC-INTERNAL-1", (
        f"Expected cycle_id='CYC-INTERNAL-1', got {called.get('cycle_id')!r}"
    )


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
    assert called["step_results"][0][0] == "pass"
    assert called["step_results"][1][0] == "fail"
    assert called["case_status"] == "fail"


# ----- run-agent step selection ---------------------------------------------


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


# ----- GET /config ----------------------------------------------------------


def test_config_returns_default_cycle(client, monkeypatch):
    monkeypatch.setenv("QMETRY_DEFAULT_CYCLE", "1ZwYH2ObF7AGZa")
    r = client.get("/config")
    assert r.status_code == 200
    assert r.json()["default_cycle"] == "1ZwYH2ObF7AGZa"


def test_config_default_cycle_null_when_unset(client, monkeypatch):
    monkeypatch.delenv("QMETRY_DEFAULT_CYCLE", raising=False)
    r = client.get("/config")
    assert r.status_code == 200
    assert r.json()["default_cycle"] is None


def test_html_responses_are_not_cached(client):
    """index.html must revalidate â€” stale HTML pins users to old bundles."""
    r = client.get("/")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", "")


# ----- GET /cycles ----------------------------------------------------------


def test_cycles_lists_qmetry_cycles(client, monkeypatch):
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    fake_client = MagicMock()
    fake_client.search_test_cycles = AsyncMock(return_value={
        "total": 430,
        "page_size": 1,
        "rows": [{"id": "aaa", "key": "SOUSCLOUD-TR-490", "name": "Smoke test"}],
    })
    monkeypatch.setattr(server_mod, "_make_qmetry_client", lambda: fake_client)
    r = client.get("/cycles")
    assert r.status_code == 200
    body = r.json()
    assert body["cycles"] == [
        {"id": "aaa", "key": "SOUSCLOUD-TR-490", "name": "Smoke test"}
    ]
    assert body["total"] == 430


def test_cycles_next_start_counts_rows_the_server_dropped(client, monkeypatch):
    """A page can return more rows than it yields (archived cycles). Paging must
    advance by what QMetry returned, or Load more skips records."""
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    fake_client = MagicMock()
    fake_client.search_test_cycles = AsyncMock(return_value={
        "total": 430,
        "page_size": 50,   # QMetry returned 50 …
        "rows": [{"id": "a", "key": "TR-1", "name": "One"}],  # … 1 survived
    })
    monkeypatch.setattr(server_mod, "_make_qmetry_client", lambda: fake_client)
    body = client.get("/cycles?start=0&limit=50").json()
    assert body["next_start"] == 50


def test_cycles_passes_query_and_paging_through(client, monkeypatch):
    """Search must reach QMetry, not filter the page in hand — there are far
    more runs than any one page holds."""
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    fake_client = MagicMock()
    fake_client.search_test_cycles = AsyncMock(
        return_value={"total": 0, "page_size": 0, "rows": []}
    )
    monkeypatch.setattr(server_mod, "_make_qmetry_client", lambda: fake_client)
    r = client.get("/cycles?q=regression&start=50&limit=25")
    assert r.status_code == 200
    fake_client.search_test_cycles.assert_awaited_once_with(
        query="regression", start_at=50, max_results=25
    )


def test_cycles_blank_query_is_not_a_filter(client, monkeypatch):
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    fake_client = MagicMock()
    fake_client.search_test_cycles = AsyncMock(
        return_value={"total": 0, "page_size": 0, "rows": []}
    )
    monkeypatch.setattr(server_mod, "_make_qmetry_client", lambda: fake_client)
    client.get("/cycles?q=")
    assert fake_client.search_test_cycles.await_args.kwargs["query"] is None


# ----- GET /testcases -------------------------------------------------------


def test_testcases_lists_project_library_with_plan_keys(client, monkeypatch):
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    fake_client = MagicMock()
    fake_client.search_project_test_cases = AsyncMock(return_value={
        "total": 2534,
        "page_size": 1,
        "rows": [{"id": "abc", "key": "SOUSCLOUD-TC-2", "name": "Login page"}],
    })
    monkeypatch.setattr(server_mod, "_make_qmetry_client", lambda: fake_client)
    r = client.get("/testcases?q=login&start=0&limit=50")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2534
    assert body["cases"] == [
        {
            "id": "abc",
            "key": "SOUSCLOUD-TC-2",
            "name": "Login page",
            # The key the console opens the case with — a one-case plan.
            "plan_key": "TC:SOUSCLOUD-TC-2",
        }
    ]
    fake_client.search_project_test_cases.assert_awaited_once_with(
        query="login", start_at=0, max_results=50
    )


def test_testcases_empty_without_qmetry(client, monkeypatch):
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    r = client.get("/testcases")
    assert r.status_code == 200
    assert r.json() == {
        "cases": [], "total": 0, "start": 0, "limit": 50, "next_start": 0,
        "truncated": False,
    }


def test_cycles_empty_without_qmetry(client, monkeypatch):
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: False)
    r = client.get("/cycles")
    assert r.status_code == 200
    assert r.json()["cycles"] == []
    assert r.json()["total"] == 0


# ----- _qmetry_execution_mode -----------------------------------------------


def test_qmetry_execution_mode_defaults_to_edit(monkeypatch):
    monkeypatch.delenv("QMETRY_EXECUTION_MODE", raising=False)
    assert server_mod._qmetry_execution_mode() == "edit"


def test_qmetry_execution_mode_create(monkeypatch):
    monkeypatch.setenv("QMETRY_EXECUTION_MODE", "CREATE")
    assert server_mod._qmetry_execution_mode() == "create"


def test_qmetry_execution_mode_garbage_is_edit(monkeypatch):
    monkeypatch.setenv("QMETRY_EXECUTION_MODE", "banana")
    assert server_mod._qmetry_execution_mode() == "edit"


# ----- POST /runs/{id}/push-qmetry ------------------------------------------


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


def test_run_push_qmetry_body_mode_overrides_env(client, monkeypatch):
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

    r = client.post(f"/runs/{state.run_id}/push-qmetry", json={"mode": "create"})
    assert r.status_code == 200
    assert calls[0]["mode"] == "create"


def test_run_push_qmetry_no_body_falls_back_to_env(client, monkeypatch):
    from agent.run_state import new_run_state, TestCase, Step
    monkeypatch.setattr(server_mod, "_qmetry_configured", lambda: True)
    monkeypatch.setattr(server_mod, "_qmetry_execution_mode", lambda: "create")

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
    assert calls[0]["mode"] == "create"


def test_post_runs_records_credentials_for_the_run(client):
    with patch.object(server_mod, "_run_in_background", new=AsyncMock()):
        r = client.post(
            "/runs",
            json={"plan": "SOUSCLOUD-TR-482", "username": "qa@duke", "password": "pw"},
        )
    run_id = r.json()["run_id"]
    assert server_mod.RUN_CREDENTIALS[run_id] == ("qa@duke", "pw")


def test_post_runs_ignores_a_half_filled_login(client):
    with patch.object(server_mod, "_run_in_background", new=AsyncMock()):
        r = client.post(
            "/runs", json={"plan": "P", "username": "qa@duke", "password": ""}
        )
    assert r.json()["run_id"] not in server_mod.RUN_CREDENTIALS


def test_get_run_never_exposes_credentials(client):
    with patch.object(server_mod, "_run_in_background", new=AsyncMock()):
        r = client.post(
            "/runs", json={"plan": "P", "username": "qa@duke", "password": "s3cret"}
        )
    # POST /runs itself must not echo the credentials it just recorded.
    assert "s3cret" not in r.text
    assert "qa@duke" not in r.text
    body = client.get(f"/runs/{r.json()['run_id']}").text
    assert "s3cret" not in body
    assert "qa@duke" not in body


def test_post_runs_malformed_body_never_echoes_password(client):
    """A validation 422 (missing `plan`) must not echo the plaintext password.

    Pydantic v2's `missing` error attaches the whole request body as `input`,
    and FastAPI's default handler serializes it verbatim — so a malformed
    POST /runs would otherwise leak the password straight back in the 422
    body. Assert on .text (the serialized wire body), not .json()["detail"],
    since the point is that the secret is nowhere in the response at all.
    """
    r = client.post(
        "/runs", json={"username": "qa@duke", "password": "s3cretLEAK"}
    )
    assert r.status_code == 422
    assert "s3cretLEAK" not in r.text


def test_run_credentials_cleared_when_run_plan_crashes():
    """The finally in _run_in_background must clear RUN_CREDENTIALS even when
    orch.run_plan raises — otherwise a crashed run leaves a credential resident."""

    class CrashingOrch:
        async def run_plan(self, plan_key, credentials=None, case_credentials=None):
            raise RuntimeError("boom")

    with patch.object(server_mod, "_build_orchestrator", lambda on_update: CrashingOrch()):
        state = new_run_state("P")
        server_mod.RUN_CREDENTIALS[state.run_id] = ("qa@duke", "pw")
        import asyncio

        asyncio.run(server_mod._run_in_background(state.run_id, "P", state))

    assert state.run_id not in server_mod.RUN_CREDENTIALS


@pytest.mark.asyncio
async def test_run_in_background_forwards_credentials_then_clears_them(monkeypatch):
    captured = {}

    class FakeOrch:
        async def run_plan(self, plan_key, credentials=None, case_credentials=None):
            captured["credentials"] = credentials
            captured["case_credentials"] = case_credentials
            return new_run_state(plan_key)

    monkeypatch.setattr(server_mod, "_build_orchestrator", lambda on_update: FakeOrch())
    monkeypatch.setattr(server_mod, "_manual_case_credentials", lambda plan: {})
    state = new_run_state("P")
    server_mod.RUN_CREDENTIALS[state.run_id] = ("qa@duke", "pw")

    await server_mod._run_in_background(state.run_id, "P", state)

    assert captured["credentials"] == ("qa@duke", "pw")
    assert state.run_id not in server_mod.RUN_CREDENTIALS


def test_manual_case_credentials_collects_only_complete_logins(monkeypatch):
    class _Mark:
        def __init__(self, user, pw):
            self.login_username = user
            self.login_password = pw

    class _Case:
        def __init__(self, case_id, mark):
            self.id = case_id
            self.mark = mark

    class _Session:
        cases = [
            _Case("TC-2", _Mark("a@duke", "pw")),
            _Case("TC-3", _Mark("b@duke", "")),      # no password — skipped
            _Case("TC-4", _Mark("", "")),            # nothing saved — skipped
        ]

    monkeypatch.setattr(server_mod.MANUAL, "get", lambda plan: _Session())
    assert server_mod._manual_case_credentials("P") == {"TC-2": ("a@duke", "pw")}


def test_manual_case_credentials_empty_when_no_session(monkeypatch):
    monkeypatch.setattr(server_mod.MANUAL, "get", lambda plan: None)
    assert server_mod._manual_case_credentials("P") == {}
