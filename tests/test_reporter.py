"""Reporter tests — verify the HTML output is well-formed and includes the
expected stats + case rows. Uses a temp dir to avoid littering reports/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.reporter import _render, generate_report
from agent.run_state import Step, TestCase, new_run_state


def _state_with_results():
    state = new_run_state("SOUSCLOUD-TP-45", "Inventory · smoke test")
    a = TestCase(id="A", name="Alpha", status="pass", steps=[
        Step(action="Navigate", detail="goto /x", status="pass",
             evaluation="Loaded", duration_seconds=1.2),
    ])
    b = TestCase(id="B", name="Bravo", status="fail", steps=[
        Step(action="Click", detail="click [data-test=save]", status="fail",
             evaluation="Expected dialog — none appeared", duration_seconds=3.0),
    ])
    state.add_case(a)
    state.add_case(b)
    state.start_run()
    state.finish()
    return state


def test_render_includes_plan_and_summary():
    state = _state_with_results()
    out = _render(state)
    assert "SOUSCLOUD-TP-45" in out
    assert "Inventory · smoke test" in out
    # totals reflect summary
    assert ">2<" in out  # total
    assert "Passed" in out
    assert "Failed" in out


def test_render_includes_each_case_and_step():
    state = _state_with_results()
    out = _render(state)
    assert ">A<" in out and ">B<" in out
    assert "Navigate" in out and "Click" in out
    assert "Loaded" in out
    assert "Expected dialog" in out


def test_render_escapes_user_text():
    state = new_run_state("X")
    state.add_case(TestCase(id="X", name="<script>alert(1)</script>", status="pass"))
    state.start_run()
    state.finish()
    out = _render(state)
    assert "<script>" not in out  # was escaped
    assert "&lt;script&gt;" in out


def test_render_includes_step_test_data():
    """Slice 3 separated test_data from step.action; the reporter must still
    surface it so a bug/report row states WHAT to type, not just the verb."""
    state = new_run_state("X")
    state.add_case(TestCase(id="A", name="Alpha", status="pass", steps=[
        Step(action="Fill recipe name", detail="fill [data-test=recipe-name]",
             status="pass", evaluation="Accepted", duration_seconds=0.5,
             test_data="Grilled Salmon"),
    ]))
    state.start_run()
    state.finish()
    out = _render(state)
    assert "Test data" in out
    assert "Grilled Salmon" in out


def test_render_omits_test_data_row_when_absent():
    state = _state_with_results()  # steps have no test_data
    out = _render(state)
    assert "Test data" not in out


def test_generate_report_writes_html_file(tmp_path, monkeypatch):
    state = _state_with_results()
    monkeypatch.setattr("agent.reporter.REPORTS_DIR", tmp_path)
    path = generate_report(state)
    assert path.exists()
    assert path.suffix == ".html"
    assert "SOUSCLOUD-TP-45" in path.read_text(encoding="utf-8")
