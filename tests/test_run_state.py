"""Asserts the serialized run_state JSON shape matches FRONTEND.md exactly.

If this test fails, the frontend will break. Update FRONTEND.md, run_state.py,
and the frontend hook in the same change.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.run_state import Plan, RunState, Step, TestCase, new_run_state

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_run_state.json"


# ---- top-level shape ----------------------------------------------------


def test_to_dict_has_all_top_level_keys():
    state = new_run_state("SOUSCLOUD-TP-45", "Inventory · smoke test")
    d = state.to_dict()
    assert set(d.keys()) == {
        "run_id",
        "plan",
        "status",
        "elapsed_seconds",
        "summary",
        "test_cases",
    }


def test_plan_shape():
    state = new_run_state("SOUSCLOUD-TP-45", "Inventory · smoke test")
    assert state.to_dict()["plan"] == {
        "key": "SOUSCLOUD-TP-45",
        "name": "Inventory · smoke test",
    }


def test_summary_shape():
    state = new_run_state("SOUSCLOUD-TP-45")
    s = state.to_dict()["summary"]
    assert set(s.keys()) == {"total", "passed", "failed", "blocked"}
    assert s == {"total": 0, "passed": 0, "failed": 0, "blocked": 0}


def test_status_defaults_to_idle():
    state = new_run_state("X")
    assert state.to_dict()["status"] == "idle"


# ---- nested case + step shape ------------------------------------------


def test_case_and_step_shape():
    state = new_run_state("X")
    case = TestCase(id="IRHS-R-01", name="Create recipe")
    state.add_case(case)
    step = Step(
        action="Navigate to Inventory",
        detail="goto /inventory/recipes",
        status="pass",
        evaluation="Page loaded",
        duration_seconds=1.2,
    )
    case.steps.append(step)
    d = state.to_dict()
    case_d = d["test_cases"][0]
    assert set(case_d.keys()) == {
        "id",
        "name",
        "status",
        "precondition",
        "test_data",
        "steps",
    }
    step_d = case_d["steps"][0]
    assert set(step_d.keys()) == {
        "action",
        "detail",
        "status",
        "evaluation",
        "duration_seconds",
        "screenshot_b64",
        "test_data",
    }


def test_case_carries_precondition_and_test_data():
    state = new_run_state("X")
    state.add_case(
        TestCase(
            id="SOUSCLOUD-TC-1985",
            name="Edit inventory",
            precondition="User is signed in as Admin",
            test_data=[{"name": "User Role", "value": "Admin"}],
        )
    )
    case_d = state.to_dict()["test_cases"][0]
    assert case_d["precondition"] == "User is signed in as Admin"
    assert case_d["test_data"] == [{"name": "User Role", "value": "Admin"}]


def test_case_defaults_precondition_null_and_test_data_empty():
    state = new_run_state("X")
    state.add_case(TestCase(id="TC-1", name="No context"))
    case_d = state.to_dict()["test_cases"][0]
    assert case_d["precondition"] is None
    assert case_d["test_data"] == []


def test_step_carries_its_own_test_data():
    state = new_run_state("X")
    state.add_case(TestCase(id="TC-1", name="c"))
    state.add_step("TC-1", Step(action="Type the name", detail="…", test_data="Recipe A"))
    step_d = state.to_dict()["test_cases"][0]["steps"][0]
    assert step_d["test_data"] == "Recipe A"


# ---- transitions update summary ----------------------------------------


def test_summary_updates_with_case_status():
    state = new_run_state("X")
    state.add_case(TestCase(id="A", name="a", status="pass"))
    state.add_case(TestCase(id="B", name="b", status="fail"))
    state.add_case(TestCase(id="C", name="c", status="blocked"))
    state.add_case(TestCase(id="D", name="d", status="queued"))
    s = state.to_dict()["summary"]
    assert s == {"total": 4, "passed": 1, "failed": 1, "blocked": 1}


# ---- fixture parity -----------------------------------------------------


def test_fixture_matches_documented_shape():
    """The fixture file the frontend builds against must have the same shape."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert {"plan", "status", "elapsed_seconds", "summary", "test_cases"}.issubset(
        data.keys()
    )
    assert set(data["plan"].keys()) >= {"key", "name"}
    assert set(data["summary"].keys()) >= {"total", "passed", "failed"}
    for case in data["test_cases"]:
        assert {
            "id",
            "name",
            "status",
            "precondition",
            "test_data",
            "steps",
        }.issubset(case.keys())
        assert case["status"] in {"queued", "running", "pass", "fail", "blocked"}
        for step in case["steps"]:
            assert {
                "action",
                "detail",
                "status",
                "evaluation",
                "duration_seconds",
                "screenshot_b64",
                "test_data",
            }.issubset(step.keys())
            assert step["status"] in {"running", "pass", "fail", "blocked"}


def test_frontend_fixture_copy_is_identical():
    """The copy Vite serves in dev (frontend/public/fixtures/) must match the
    backend's fixture exactly, or the console renders stale/wrong shape in
    dev mode. Compare parsed JSON — the repo has CRLF/LF differences between
    files elsewhere, so a raw byte compare would be a false negative here."""
    root = Path(__file__).resolve().parent.parent
    backend = json.loads((root / "fixtures" / "sample_run_state.json").read_text(encoding="utf-8"))
    frontend = json.loads(
        (root / "frontend" / "public" / "fixtures" / "sample_run_state.json").read_text(encoding="utf-8")
    )
    assert backend == frontend
