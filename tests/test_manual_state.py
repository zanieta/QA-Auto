from __future__ import annotations

import json

import pytest

from agent.manual_state import (
    ManualStore,
    compose_comment,
    derive_case_status,
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


def test_compose_comment_includes_step_marks(store):
    # NOTE: was test_compose_comment_includes_failed_steps — the "Fail at: step
    # N — <action>" lines derived from mark.failed_steps are replaced by
    # per-step-mark lines (spec 2026-07-09, section 1).
    s = store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_mark("TP-45", "IRHS-R-01", "fail", "Save did nothing", [])
    store.set_step_mark("TP-45", "IRHS-R-01", 1, "fail", "Toast never appeared", None)
    case = store.get("TP-45").find_case("IRHS-R-01")
    text = compose_comment(case)
    assert "Save did nothing" in text
    assert "Step 2: fail — Toast never appeared" in text


def test_set_agent_and_mark_pushed(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-abc123")
    store.set_mark("TP-45", "IRHS-R-01", "pass", "", [])
    store.mark_pushed("TP-45", "IRHS-R-01")
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.agent_status == "running"
    assert case.mark.agent_run_id == "run-abc123"
    assert case.mark.pushed_to_qmetry is True


def test_agent_verdict_becomes_the_case_status(store):
    """The console has no per-step mark buttons — the agent's pass/fail IS the
    result. Without this the status would stay "unmarked" and the QMetry push,
    which is gated on something being marked, could never unlock."""
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.status == "unmarked"

    store.set_agent("TP-45", "IRHS-R-01", "running", "run-1")
    assert case.mark.status == "unmarked"  # a run in flight is not a verdict

    store.set_agent("TP-45", "IRHS-R-01", "fail", "run-1")
    assert case.mark.status == "fail"


def test_agent_verdict_does_not_overwrite_a_hand_mark(store):
    """A human ruling on a step outranks the AI — re-running the agent must not
    quietly replace it."""
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_step_mark("TP-45", "IRHS-R-01", 0, "pass", "checked by hand")
    store.set_agent("TP-45", "IRHS-R-01", "fail", "run-1")
    assert store.get("TP-45").find_case("IRHS-R-01").mark.status == "pass"


def test_step_test_data_is_serialized_per_step(store):
    """Test data lives on individual steps (QMetry has no case-level value), and
    a step without any serializes as "" so the UI can render "none"."""
    raw = [{
        "id": "A", "name": "Case A",
        "steps": [
            {"action": "Enter time", "expected": "ok", "test_data": "Time: 45"},
            {"action": "Save", "expected": "saved"},
        ],
    }]
    store.build("TP-45", "Smoke", raw, qmetry_configured=False)
    steps = store.get("TP-45").find_case("A").to_dict()["steps"]
    assert steps[0]["test_data"] == "Time: 45"
    assert steps[1]["test_data"] == ""


_RAW_CASES_WITH_CYCLE = [
    {
        "id": "CYC-01",
        "name": "Cycle case",
        "steps": [{"action": "Open page", "expected": "Page loads"}],
        "_qmetry_cycle_id": "CYC-INTERNAL-1",
        "_qmetry_execution_id": 555,
    },
    {
        "id": "CYC-02",
        "name": "No-cycle case",
        "steps": [],
    },
]


def test_build_carries_execution_cycle_id_server_side(store):
    """ManualStore.build populates execution_cycle_id from _qmetry_cycle_id."""
    s = store.build("TP-CYC", "Cycle smoke", _RAW_CASES_WITH_CYCLE, qmetry_configured=True)
    # server-side field is present
    assert s.find_case("CYC-01").execution_cycle_id == "CYC-INTERNAL-1"
    # case without _qmetry_cycle_id gets None
    assert s.find_case("CYC-02").execution_cycle_id is None


def test_execution_cycle_id_not_in_to_dict(store):
    """execution_cycle_id MUST NOT appear in to_dict() (browser-visible) output."""
    s = store.build("TP-CYC", "Cycle smoke", _RAW_CASES_WITH_CYCLE, qmetry_configured=True)
    case_dict = s.find_case("CYC-01").to_dict()
    assert "execution_cycle_id" not in case_dict
    assert "_qmetry_cycle_id" not in case_dict
    # also check the full session serialization
    session_dict = s.to_dict()
    for c in session_dict["cases"]:
        assert "execution_cycle_id" not in c
        assert "_qmetry_cycle_id" not in c


# ---------------------------------------------------------------------------
# Task 2 — parity tests
# ---------------------------------------------------------------------------
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


# ----- agent_steps (step-selection agent runs) ------------------------------


def test_agent_steps_serializes_and_snapshots(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=False)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-1", agent_steps=[0, 1])

    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.agent_steps == [0, 1]
    assert case.to_dict()["manual"]["agent_steps"] == [0, 1]

    # snapshot round-trip: a fresh store re-reads the persisted selection
    store2 = ManualStore()
    store2.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=False)
    assert store2.get("TP-45").find_case("IRHS-R-01").mark.agent_steps == [0, 1]


def test_set_agent_without_steps_preserves_selection(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=False)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-1", agent_steps=[1])
    # completion callback omits agent_steps - selection must survive
    store.set_agent("TP-45", "IRHS-R-01", "pass", "run-1")
    assert store.get("TP-45").find_case("IRHS-R-01").mark.agent_steps == [1]
    # explicit None clears it (a full run)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-2", agent_steps=None)
    assert store.get("TP-45").find_case("IRHS-R-01").mark.agent_steps is None


# ----- agent_note (task 1) -------------------------------------------------


def test_agent_note_serializes_and_defaults_empty(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=False)
    store.set_agent("TP-45", "IRHS-R-01", "pass", "run-1", agent_note="Step 1: pass — ok")
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.to_dict()["agent_note"] == "Step 1: pass — ok"
    # old snapshots without the key load as ""
    from agent.manual_state import ManualMark
    assert ManualMark.from_dict({"status": "pass"}).agent_note == ""


def test_set_agent_without_note_preserves_note(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=False)
    store.set_agent("TP-45", "IRHS-R-01", "running", "run-1", agent_note="kept")
    store.set_agent("TP-45", "IRHS-R-01", "pass", "run-1")
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.agent_note == "kept"


def test_compose_comment_appends_agent_note(store):
    # NOTE: updated for the step-marks rewrite — asserts "Step 1: fail" (from
    # set_step_mark) instead of the old "Fail at: step 1" (from failed_steps).
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=False)
    case = store.set_mark("TP-45", "IRHS-R-01", "fail", "flaky toggle", [0])
    store.set_step_mark("TP-45", "IRHS-R-01", 0, "fail", "", None)
    store.set_agent("TP-45", "IRHS-R-01", "fail", "run-1", agent_note="Agent run x")
    text = compose_comment(case)
    assert text.startswith("flaky toggle")
    assert "Step 1: fail" in text
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


# ----- precondition and credentials (task 1) --------------------------------


def test_precondition_on_case_to_dict(store):
    # rebuild with a precondition on the raw case
    session = store.build(
        "TP-46", "TP-46",
        [{"id": "C-1", "name": "Case", "steps": [], "precondition": "User has valid Recipe Admin credentials."}],
        qmetry_configured=False,
    )
    d = session.find_case("C-1").to_dict()
    assert d["precondition"] == "User has valid Recipe Admin credentials."


def test_credentials_never_in_browser_payload(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_credentials("TP-45", "IRHS-R-01", "qa.user@dukemfg.com", "s3cret")
    d = store.get("TP-45").find_case("IRHS-R-01").mark.to_dict()
    assert d["login_username"] == "qa.user@dukemfg.com"
    assert d["has_password"] is True
    assert "login_password" not in d
    assert "s3cret" not in str(d)


def test_credentials_persist_and_roundtrip(store):
    from agent.manual_state import ManualMark
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_credentials("TP-45", "IRHS-R-01", "u@x.com", "pw")
    persisted = store.get("TP-45").find_case("IRHS-R-01").mark.to_dict(include_secrets=True)
    assert persisted["login_password"] == "pw"
    again = ManualMark.from_dict(persisted)
    assert (again.login_username, again.login_password) == ("u@x.com", "pw")


def test_set_credentials_clear_and_keep_semantics(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_credentials("TP-45", "IRHS-R-01", "u@x.com", "pw")
    # username-only change keeps the password
    store.set_credentials("TP-45", "IRHS-R-01", "new@x.com", "")
    m = store.get("TP-45").find_case("IRHS-R-01").mark
    assert (m.login_username, m.login_password) == ("new@x.com", "pw")
    # both empty clears both
    store.set_credentials("TP-45", "IRHS-R-01", "", "")
    m = store.get("TP-45").find_case("IRHS-R-01").mark
    assert (m.login_username, m.login_password) == ("", "")


def test_target_url_set_clear_and_persist_roundtrip(store):
    from agent.manual_state import ManualMark

    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_target_url("TP-45", "IRHS-R-01", "https://test.souscheftech.com/login")
    d = store.get("TP-45").find_case("IRHS-R-01").mark.to_dict()
    assert d["target_url"] == "https://test.souscheftech.com/login"

    persisted = store.get("TP-45").find_case("IRHS-R-01").mark.to_dict(include_secrets=True)
    again = ManualMark.from_dict(persisted)
    assert again.target_url == "https://test.souscheftech.com/login"

    # empty clears back to the .env default
    store.set_target_url("TP-45", "IRHS-R-01", "")
    m = store.get("TP-45").find_case("IRHS-R-01").mark
    assert m.target_url == ""


def test_target_url_missing_key_loads_as_empty_string():
    from agent.manual_state import ManualMark

    m = ManualMark.from_dict({"status": "unmarked"})
    assert m.target_url == ""
    assert m.to_dict()["target_url"] == ""


# ----- per-step marks (task 1, spec 2026-07-09) -----------------------------


def test_set_step_mark_round_trip_and_persists(store, tmp_path):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    case = store.set_step_mark("TP-45", "IRHS-R-01", 1, "fail", "Save did nothing", None)
    assert case.mark.step_marks["1"] == {
        "status": "fail",
        "note": "Save did nothing",
        "agent_status": None,
        "overrode": False,
    }
    assert case.mark.status == "fail"
    assert case.mark.failed_steps == [1]
    snapshot = json.loads((tmp_path / "TP-45.json").read_text(encoding="utf-8"))
    assert snapshot["IRHS-R-01"]["step_marks"]["1"]["status"] == "fail"


def test_set_step_mark_invalid_status_raises(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    with pytest.raises(ValueError):
        store.set_step_mark("TP-45", "IRHS-R-01", 0, "bogus", "", None)


def test_set_step_mark_overrode_flag(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    case = store.set_step_mark("TP-45", "IRHS-R-01", 0, "pass", "Looks fine to me", "blocked")
    assert case.mark.step_marks["0"]["overrode"] is True
    case2 = store.set_step_mark("TP-45", "IRHS-R-01", 1, "pass", "", "pass")
    assert case2.mark.step_marks["1"]["overrode"] is False  # agrees with the agent
    case3 = store.set_step_mark("TP-45", "IRHS-R-01", 1, "fail", "", None)
    assert case3.mark.step_marks["1"]["overrode"] is False  # no agent verdict -> never an override


def test_derive_case_status_precedence():
    assert derive_case_status({}) == "unmarked"
    assert derive_case_status({"0": {"status": "skip"}}) == "unmarked"
    assert derive_case_status({"0": {"status": "skip"}, "1": {"status": "skip"}}) == "unmarked"
    assert derive_case_status({"0": {"status": "pass"}}) == "pass"
    assert derive_case_status({"0": {"status": "pass"}, "1": {"status": "skip"}}) == "pass"
    assert derive_case_status({"0": {"status": "pass"}, "1": {"status": "blocked"}}) == "blocked"
    assert (
        derive_case_status(
            {"0": {"status": "pass"}, "1": {"status": "blocked"}, "2": {"status": "fail"}}
        )
        == "fail"
    )


def test_set_step_mark_derives_case_status(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_step_mark("TP-45", "IRHS-R-01", 0, "skip", "", None)
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.status == "unmarked"
    store.set_step_mark("TP-45", "IRHS-R-01", 1, "blocked", "", None)
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert case.mark.status == "blocked"


def test_step_marks_survive_rebuild(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_step_mark("TP-45", "IRHS-R-01", 0, "pass", "", None)
    s = store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    assert s.find_case("IRHS-R-01").mark.step_marks["0"]["status"] == "pass"


def test_step_marks_default_empty_for_old_snapshots():
    from agent.manual_state import ManualMark

    m = ManualMark.from_dict({"status": "pass"})
    assert m.step_marks == {}


def test_step_marks_serialize_in_both_payload_variants(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_step_mark("TP-45", "IRHS-R-01", 0, "fail", "note", None)
    case = store.get("TP-45").find_case("IRHS-R-01")
    assert "step_marks" in case.mark.to_dict()
    assert "step_marks" in case.mark.to_dict(include_secrets=True)


def test_compose_comment_step_marks_with_note_override_and_skip(store):
    store.build("TP-45", "Smoke", RAW_CASES, qmetry_configured=True)
    store.set_mark("TP-45", "IRHS-R-01", "fail", "Overall broken", [])
    store.set_step_mark("TP-45", "IRHS-R-01", 0, "pass", "Looked fine", "blocked")
    store.set_step_mark("TP-45", "IRHS-R-01", 1, "skip", "", None)
    case = store.get("TP-45").find_case("IRHS-R-01")
    text = compose_comment(case)
    lines = text.splitlines()
    assert lines[0] == "Overall broken"
    assert lines[1] == "Step 1: pass — Looked fine (overrode agent: blocked)"
    assert lines[2] == "Step 2: skip"
