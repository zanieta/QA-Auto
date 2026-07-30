from __future__ import annotations

import json

import pytest

from agent.knowledge import lookup_guidance, record_override


@pytest.fixture(autouse=True)
def knowledge_path(tmp_path, monkeypatch):
    """Point the module at a throwaway file for every test in this module."""
    path = tmp_path / "knowledge" / "eval_overrides.jsonl"
    monkeypatch.setattr("agent.knowledge.KNOWLEDGE_PATH", path)
    return path


def test_missing_file_returns_empty_string(knowledge_path):
    assert not knowledge_path.exists()
    assert lookup_guidance("IRHS-R-01", 4, "Click Save") == ""


def test_record_creates_parent_dir_and_appends_json_line(knowledge_path):
    assert not knowledge_path.parent.exists()
    record_override(
        "SOUSCLOUD-TP-45",
        "IRHS-R-01",
        4,
        "Click Save",
        "Toast appears",
        "blocked",
        "pass",
        "toast is there, just slow to render",
        "2026-07-09T10:00:00Z",
    )
    assert knowledge_path.exists()
    lines = knowledge_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry == {
        "plan": "SOUSCLOUD-TP-45",
        "case_id": "IRHS-R-01",
        "step_index": 4,
        "step_text": "Click Save",
        "expected": "Toast appears",
        "agent_status": "blocked",
        "human_status": "pass",
        "note": "toast is there, just slow to render",
        "when": "2026-07-09T10:00:00Z",
    }


def test_lookup_round_trip_formats_guidance():
    record_override(
        "TP-45", "IRHS-R-01", 4, "Click Save", "Toast appears",
        "blocked", "pass", "toast is there, just slow to render", "2026-07-09T10:00:00Z",
    )
    guidance = lookup_guidance("IRHS-R-01", 4, "Click Save")
    assert guidance == (
        "- tester overrode the AI's 'blocked' to 'pass': "
        "toast is there, just slow to render"
    )


def test_lookup_whitespace_normalized_text_still_matches():
    record_override(
        "TP-45", "IRHS-R-01", 4, "Click   Save\nbutton", "Toast appears",
        "fail", "pass", "known flaky toast", "2026-07-09T10:00:00Z",
    )
    # Same words, different whitespace/newlines than what was recorded.
    guidance = lookup_guidance("IRHS-R-01", 4, "Click Save button")
    assert "known flaky toast" in guidance


def test_lookup_excludes_mismatched_step_text():
    record_override(
        "TP-45", "IRHS-R-01", 4, "Click Save", "Toast appears",
        "blocked", "pass", "note here", "2026-07-09T10:00:00Z",
    )
    # Step text changed in QMetry -> stale guidance must not apply.
    assert lookup_guidance("IRHS-R-01", 4, "Click Cancel") == ""


def test_lookup_excludes_mismatched_case_id():
    record_override(
        "TP-45", "IRHS-R-01", 4, "Click Save", "Toast appears",
        "blocked", "pass", "note here", "2026-07-09T10:00:00Z",
    )
    assert lookup_guidance("IRHS-R-02", 4, "Click Save") == ""


def test_lookup_excludes_mismatched_step_index():
    record_override(
        "TP-45", "IRHS-R-01", 4, "Click Save", "Toast appears",
        "blocked", "pass", "note here", "2026-07-09T10:00:00Z",
    )
    assert lookup_guidance("IRHS-R-01", 5, "Click Save") == ""


def test_lookup_caps_at_newest_three_in_file_order():
    for i in range(5):
        record_override(
            "TP-45", "IRHS-R-01", 4, "Click Save", "Toast appears",
            "blocked", "pass", f"note {i}", "2026-07-09T10:00:00Z",
        )
    guidance = lookup_guidance("IRHS-R-01", 4, "Click Save")
    lines = guidance.split("\n")
    assert len(lines) == 3
    assert "note 2" in lines[0]
    assert "note 3" in lines[1]
    assert "note 4" in lines[2]


def test_lookup_skips_corrupt_json_lines(knowledge_path):
    knowledge_path.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({
        "plan": "TP-45",
        "case_id": "IRHS-R-01",
        "step_index": 4,
        "step_text": "Click Save",
        "expected": "Toast appears",
        "agent_status": "blocked",
        "human_status": "pass",
        "note": "still works",
        "when": "2026-07-09T10:00:00Z",
    })
    knowledge_path.write_text(
        "not-json-at-all\n" + good + "\n{\"broken\": \n",
        encoding="utf-8",
    )
    guidance = lookup_guidance("IRHS-R-01", 4, "Click Save")
    assert guidance == "- tester overrode the AI's 'blocked' to 'pass': still works"


def test_lookup_skips_valid_json_lines_that_are_not_objects(knowledge_path):
    # A line can parse as JSON without being an object (bare null, a number,
    # a list). Those must be skipped per-line — not discard all guidance via
    # the outer catch-all.
    knowledge_path.parent.mkdir(parents=True, exist_ok=True)

    def entry(note: str) -> str:
        return json.dumps({
            "plan": "TP-45",
            "case_id": "IRHS-R-01",
            "step_index": 4,
            "step_text": "Click Save",
            "expected": "Toast appears",
            "agent_status": "blocked",
            "human_status": "pass",
            "note": note,
            "when": "2026-07-09T10:00:00Z",
        })

    knowledge_path.write_text(
        entry("first good") + "\nnull\n42\n[1, 2]\n" + entry("second good") + "\n",
        encoding="utf-8",
    )
    guidance = lookup_guidance("IRHS-R-01", 4, "Click Save")
    assert "first good" in guidance
    assert "second good" in guidance
    assert len(guidance.split("\n")) == 2


def test_lookup_never_raises_when_path_is_a_directory(knowledge_path):
    # KNOWLEDGE_PATH pointing at a directory instead of a file makes the
    # read raise internally; lookup_guidance must swallow it and return "".
    knowledge_path.mkdir(parents=True)
    assert lookup_guidance("IRHS-R-01", 4, "Click Save") == ""


def test_lookup_no_matches_returns_empty_string():
    record_override(
        "TP-45", "IRHS-R-01", 4, "Click Save", "Toast appears",
        "blocked", "pass", "note here", "2026-07-09T10:00:00Z",
    )
    assert lookup_guidance("OTHER-CASE", 0, "Something else") == ""
