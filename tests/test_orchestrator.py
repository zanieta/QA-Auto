"""Orchestrator tests — azure + browser + case_source are all mocked.

Verifies:
  - run_plan transitions run_state in the documented order
  - on_update is called at every observable point
  - one bad case doesn't kill the rest of the run
  - empty step list yields BLOCKED for that case
  - browser open failure -> BLOCKED case
  - translator failure -> BLOCKED step
  - browser action failure -> FAIL step
  - evaluator FAIL bubbles up to FAIL case
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.azure_ai import AzureAIError
from agent.browser import BrowserError
from agent.orchestrator import Orchestrator
from agent.run_state import RunState


@pytest.fixture(autouse=True)
def mock_login():
    """Patch login() so orchestrator tests never attempt a real browser login."""
    with patch("agent.orchestrator.login", new=AsyncMock()) as m:
        yield m


# ---------- fakes -----------------------------------------------------------


class FakeCaseSource:
    def __init__(self, plan, cases):
        self._plan = plan
        self._cases = cases

    async def get_plan(self, plan_key):
        return self._plan

    async def list_cases(self, plan_key, with_steps=True):
        # Fake cases always carry their steps inline, like FixtureCaseSource.
        return [{**c, "_steps_loaded": True} for c in self._cases]

    async def get_case_steps(self, plan_key, case_id):
        match = next((c for c in self._cases if c["id"] == case_id), None)
        return match["steps"] if match else []


def _fake_browser():
    b = MagicMock()
    b.open_session = AsyncMock()
    b.close_session = AsyncMock()
    b.current_url = AsyncMock(return_value="https://app/")
    b.screenshot = AsyncMock(return_value="PNG-B64")
    b.wait_for_settle = AsyncMock()
    b.execute_action = AsyncMock()
    b.snapshot_elements = AsyncMock(return_value=[{"ref": "e1", "tag": "a", "role": "link", "name": "Go"}])
    return b


def _fake_azure(translate_side_effect=None, evaluate_side_effect=None):
    a = MagicMock()
    a.translate_step = AsyncMock(side_effect=translate_side_effect)
    a.evaluate_result = AsyncMock(side_effect=evaluate_side_effect)
    return a


def _ok_actions():
    return [{"action": "click", "selector": "#go", "value": None}]


# ---------- happy path ------------------------------------------------------


@pytest.mark.asyncio
async def test_run_plan_happy_two_cases_all_pass():
    cases = [
        {"id": "A", "name": "Alpha", "steps": [
            {"action": "Click go", "expected": "Page loaded"},
        ]},
        {"id": "B", "name": "Bravo", "steps": [
            {"action": "Click again", "expected": "Done"},
        ]},
    ]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions(), _ok_actions()],
        evaluate_side_effect=[
            {"status": "pass", "reason": "Loaded"},
            {"status": "pass", "reason": "Done"},
        ],
    )
    browser = _fake_browser()
    updates: list[dict] = []

    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: updates.append(s.to_dict()),
    )
    state: RunState = await orch.run_plan("X")

    assert state.status == "done"
    assert state.summary == {"total": 2, "passed": 2, "failed": 0, "blocked": 0}
    assert [c.status for c in state.test_cases] == ["pass", "pass"]
    # every step resolved with a non-null evaluation + duration
    for case in state.test_cases:
        for step in case.steps:
            assert step.status == "pass"
            assert step.evaluation
            assert step.duration_seconds is not None
    # browser was opened+closed once per case
    assert browser.open_session.await_count == 2
    assert browser.close_session.await_count == 2
    # frontend got many updates
    assert len(updates) > 4
    # final update has the terminal state
    assert updates[-1]["status"] == "done"


# ---------- bad-case isolation ----------------------------------------------


@pytest.mark.asyncio
async def test_one_bad_case_does_not_kill_run():
    cases = [
        {"id": "A", "name": "Alpha", "steps": [
            {"action": "Click go", "expected": "Page loaded"},
        ]},
        {"id": "B", "name": "Bravo", "steps": [
            {"action": "Click again", "expected": "Done"},
        ]},
    ]
    azure = _fake_azure(
        translate_side_effect=[
            RuntimeError("translator exploded"),  # case A
            _ok_actions(),                         # case B
        ],
        evaluate_side_effect=[
            {"status": "pass", "reason": "Done"},
        ],
    )
    browser = _fake_browser()

    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
    )
    state = await orch.run_plan("X")
    # A crashed -> blocked, B passed
    assert state.test_cases[0].status == "blocked"
    assert state.test_cases[1].status == "pass"
    assert state.status == "done"


# ---------- empty steps -----------------------------------------------------


@pytest.mark.asyncio
async def test_case_with_no_steps_is_blocked():
    cases = [{"id": "A", "name": "Alpha", "steps": []}]
    orch = Orchestrator(
        azure=_fake_azure(),
        browser_factory=lambda: _fake_browser(),
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
    )
    state = await orch.run_plan("X")
    assert state.test_cases[0].status == "blocked"
    # we should not have tried to open a browser for this case
    # (verify by re-checking that the factory returned an UNTOUCHED mock)
    # — covered by absence of step records:
    assert state.test_cases[0].steps == []


# ---------- browser open failure -------------------------------------------


@pytest.mark.asyncio
async def test_browser_open_failure_yields_blocked_case():
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click", "expected": "ok"},
    ]}]
    browser = _fake_browser()
    browser.open_session = AsyncMock(side_effect=RuntimeError("chromium gone"))

    orch = Orchestrator(
        azure=_fake_azure(),
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
    )
    state = await orch.run_plan("X")
    assert state.test_cases[0].status == "blocked"


# ---------- translator failure -> BLOCKED step ------------------------------


@pytest.mark.asyncio
async def test_translator_failure_yields_blocked_step_and_case():
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "ok"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[AzureAIError("invalid JSON")],
    )
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: _fake_browser(),
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        step_attempts=1,  # single-shot: retries would exhaust this 1-item side_effect
    )
    state = await orch.run_plan("X")
    step = state.test_cases[0].steps[0]
    assert step.status == "blocked"
    assert "translate" in (step.evaluation or "").lower()
    assert state.test_cases[0].status == "blocked"


# ---------- browser action failure -> FAIL step (heal-retry) ----------------


@pytest.mark.asyncio
async def test_action_failure_retries_once_then_fails():
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
    ]}]
    browser = _fake_browser()
    browser.execute_action = AsyncMock(side_effect=BrowserError("click failed: timeout"))

    azure = _fake_azure(translate_side_effect=[_ok_actions(), _ok_actions()])
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        step_attempts=1,  # single-shot: isolates the within-attempt heal-retry
    )
    state = await orch.run_plan("X")
    step = state.test_cases[0].steps[0]
    assert step.status == "fail"
    assert "click failed" in (step.evaluation or "")
    # healed once: snapshot + translate happened twice
    assert browser.snapshot_elements.await_count == 2
    assert azure.translate_step.await_count == 2


@pytest.mark.asyncio
async def test_action_failure_heals_and_passes_on_retry():
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
    ]}]
    browser = _fake_browser()
    # first action attempt fails, second (after re-translate) succeeds
    browser.execute_action = AsyncMock(side_effect=[BrowserError("stale"), None])

    azure = _fake_azure(
        translate_side_effect=[_ok_actions(), _ok_actions()],
        evaluate_side_effect=[{"status": "pass", "reason": "Loaded"}],
    )
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
    )
    state = await orch.run_plan("X")
    step = state.test_cases[0].steps[0]
    assert step.status == "pass"
    assert azure.translate_step.await_count == 2  # healed
    assert browser.screenshot.await_count == 1     # reached evaluation


# ---------- evaluator returns fail -> FAIL case ----------------------------


@pytest.mark.asyncio
async def test_evaluator_fail_bubbles_up_to_case():
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Confirmation dialog visible"},
    ]}]
    orch = Orchestrator(
        azure=_fake_azure(
            translate_side_effect=[_ok_actions()],
            evaluate_side_effect=[{"status": "fail", "reason": "No dialog appeared"}],
        ),
        browser_factory=lambda: _fake_browser(),
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        step_attempts=1,  # single-shot: this 1-item side_effect covers one attempt
    )
    state = await orch.run_plan("X")
    assert state.test_cases[0].steps[0].status == "fail"
    assert state.test_cases[0].status == "fail"
    assert state.summary["failed"] == 1


# ---------- run_single_case --------------------------------------------------


@pytest.mark.asyncio
async def test_run_single_case_sets_browser_credentials(mock_login):
    """Per-case credentials must land on the browser BEFORE login() awaits,
    and must never appear anywhere else (context strings, run_state, logs)."""
    cases = [
        {"id": "A", "name": "Alpha", "steps": [{"action": "x", "expected": "y"}]},
    ]
    browser = _fake_browser()
    seen: dict = {}

    async def _capture_login(b):
        # If this fires before the assignment below, this would be None —
        # proving order requires the assignment to already have happened.
        seen["credentials_at_login_time"] = b.credentials

    mock_login.side_effect = _capture_login

    orch = Orchestrator(
        azure=_fake_azure(
            translate_side_effect=[_ok_actions()],
            evaluate_side_effect=[{"status": "pass", "reason": "ok"}],
        ),
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case(
        "A", "X", credentials=("case-user@x.com", "case-pw")
    )

    assert browser.credentials == ("case-user@x.com", "case-pw")
    assert seen["credentials_at_login_time"] == ("case-user@x.com", "case-pw")
    assert state.test_cases[0].status == "pass"


@pytest.mark.asyncio
async def test_run_single_case_executes_only_named_case():
    cases = [
        {"id": "A", "name": "Alpha", "steps": [{"action": "x", "expected": "y"}]},
        {"id": "B", "name": "Bravo", "steps": [{"action": "x", "expected": "y"}]},
    ]
    orch = Orchestrator(
        azure=_fake_azure(
            translate_side_effect=[_ok_actions()],
            evaluate_side_effect=[{"status": "pass", "reason": "ok"}],
        ),
        browser_factory=lambda: _fake_browser(),
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
    )
    state = await orch.run_single_case("B", "X")
    assert len(state.test_cases) == 1
    assert state.test_cases[0].id == "B"
    assert state.test_cases[0].status == "pass"


# ----- step_indices (step-selection agent runs) ----------------------------


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


@pytest.mark.asyncio
async def test_multi_action_step_evaluates_all_frames():
    """One frame is captured per action; the evaluator sees the ordered list."""
    three_clicks = [
        {"action": "click", "selector": "#a", "value": None},
        {"action": "click", "selector": "#b", "value": None},
        {"action": "click", "selector": "#c", "value": None},
    ]
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Expand all menus", "expected": "All sub-items visible"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[three_clicks],
        evaluate_side_effect=[{"status": "pass", "reason": "ok"}],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")
    frames, expected = azure.evaluate_result.call_args.args
    assert isinstance(frames, list) and len(frames) == 3
    assert expected == "All sub-items visible"
    # the tape still carries a single (final) screenshot — contract unchanged
    assert state.test_cases[0].steps[0].screenshot_b64 == "PNG-B64"


@pytest.mark.asyncio
async def test_translator_still_receives_the_step_test_data():
    """`test_data` is a separate field now (the console labels it per step), so
    the orchestrator has to fold it back into the instruction — those values are
    exactly what the model must type."""
    cases = [{"id": "A", "name": "Cook time", "steps": [
        {"action": "Enter the cook time", "expected": "Accepted",
         "test_data": "Cook Mode Time: 45"},
        {"action": "Click Save", "expected": "Saved", "test_data": ""},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions(), _ok_actions()],
        evaluate_side_effect=[
            {"status": "pass", "reason": "ok"},
            {"status": "pass", "reason": "ok"},
        ],
    )
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: _fake_browser(),
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")

    first = azure.translate_step.call_args_list[0].args[0]
    assert first == "Enter the cook time\nTest data: Cook Mode Time: 45"
    # A step without test data gets no dangling label.
    second = azure.translate_step.call_args_list[1].args[0]
    assert second == "Click Save"
    assert "Test data" not in second
    assert state.test_cases[0].status == "pass"


@pytest.mark.asyncio
async def test_translator_receives_whole_case_context():
    """Every step translation carries the full case brief: name, all steps,
    progress so far, and the current-step marker."""
    cases = [{"id": "A", "name": "Login layout", "steps": [
        {"action": "Open the login page", "expected": "Login form visible"},
        {"action": "Sign in", "expected": "Dashboard visible"},
        {"action": "Verify the sidebar", "expected": "Menus visible"},
    ]}]
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
    # run steps 1 and 3; step 2 is left for the tester
    await orch.run_single_case("A", step_indices=[0, 2])

    ctx_first = azure.translate_step.call_args_list[0].kwargs["app_context"]
    assert "TEST CASE: A" in ctx_first and "Login layout" in ctx_first
    assert "1." in ctx_first and "Open the login page" in ctx_first
    assert "2." in ctx_first and "Sign in" in ctx_first
    assert ">> CURRENT" in ctx_first  # step 1 marked current
    assert "MANUAL" in ctx_first      # unselected step 2 marked for the tester

    ctx_second = azure.translate_step.call_args_list[1].kwargs["app_context"]
    assert "done: pass" in ctx_second   # step 1 outcome visible to step 3
    assert ">> CURRENT" in ctx_second


@pytest.mark.asyncio
async def test_translator_receives_expected_result_in_context():
    """The CURRENT step's expected result must reach the translator so it can
    apply the RECONCILE-FIRST rule (e.g. recognizing that a step like
    'Navigate to …' with expected 'the login page will appear' implies a
    logout action, not a plain navigation)."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Navigate to the account page", "expected": "the login page will appear"},
    ]}]
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
        step_attempts=1,  # single-shot: not testing retries here
    )
    await orch.run_single_case("A")

    ctx = azure.translate_step.call_args_list[0].kwargs["app_context"]
    assert "the login page will appear" in ctx
    assert "EXPECTED RESULT" in ctx


@pytest.mark.asyncio
async def test_blocked_evaluation_blocks_the_step_and_case():
    """Evaluator 'blocked' (cannot verify) must not masquerade as fail."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Verify hidden menus", "expected": "Menus hidden"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions()],
        evaluate_side_effect=[
            {"status": "blocked", "reason": "Precondition never created. Findings: setting untouched."},
        ],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
        step_attempts=1,  # single-shot: this 1-item side_effect covers one attempt
    )
    state = await orch.run_single_case("A")
    step = state.test_cases[0].steps[0]
    assert step.status == "blocked"
    assert "Findings:" in step.evaluation
    assert state.test_cases[0].status == "blocked"


# ----- continue past failed/blocked steps ------------------------------------


@pytest.mark.asyncio
async def test_failed_step_does_not_stop_remaining_steps():
    """A failed step records fail but does not stop the case; later steps
    still execute. Case outcome is fail when any step failed."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Step one", "expected": "E1"},
        {"action": "Step two", "expected": "E2"},
        {"action": "Step three", "expected": "E3"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions(), _ok_actions(), _ok_actions()],
        evaluate_side_effect=[
            {"status": "fail", "reason": "no"},
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
        step_attempts=1,  # single-shot: isolates continue-past-failure from retries
    )
    state = await orch.run_single_case("A")
    case = state.test_cases[0]
    assert [s.status for s in case.steps] == ["fail", "pass", "pass"]
    assert case.status == "fail"
    assert azure.translate_step.await_count == 3
    assert azure.evaluate_result.await_count == 3


@pytest.mark.asyncio
async def test_blocked_step_does_not_stop_remaining_steps():
    """A blocked step records blocked but does not stop the case. Case
    outcome is blocked when no step failed but one was blocked."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Step one", "expected": "E1"},
        {"action": "Step two", "expected": "E2"},
        {"action": "Step three", "expected": "E3"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions(), _ok_actions(), _ok_actions()],
        evaluate_side_effect=[
            {"status": "pass", "reason": "ok"},
            {"status": "blocked", "reason": "cannot verify"},
            {"status": "pass", "reason": "ok"},
        ],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
        step_attempts=1,  # single-shot: isolates continue-past-blocked from retries
    )
    state = await orch.run_single_case("A")
    case = state.test_cases[0]
    assert [s.status for s in case.steps] == ["pass", "blocked", "pass"]
    assert case.status == "blocked"
    assert azure.translate_step.await_count == 3
    assert azure.evaluate_result.await_count == 3


# ----- act -> observe loop ---------------------------------------------------


@pytest.mark.asyncio
async def test_navigation_triggers_reobserve_with_progress():
    """A click that navigates stales the refs: the loop must re-snapshot,
    re-translate with PROGRESS context, and stop when the model says done."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click each menu to visit its section", "expected": "Sections load"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[
            [{"action": "click", "selector": "#menu1", "value": None}],  # round 1 -> navigates
            [{"action": "click", "selector": "#menu2", "value": None}],  # round 2 -> navigates
            [],                                                          # round 3 -> done
        ],
        evaluate_side_effect=[{"status": "pass", "reason": "ok"}],
    )
    browser = _fake_browser()
    # URL trace: r1 ctx, r1 before, r1 after(NAV), r2 ctx, r2 before, r2 after(NAV), r3 ctx
    browser.current_url = AsyncMock(side_effect=["A", "A", "B", "B", "B", "C", "C"])
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")
    step = state.test_cases[0].steps[0]
    assert step.status == "pass"
    assert azure.translate_step.await_count == 3
    # the loop re-observed after each navigation
    assert browser.snapshot_elements.await_count == 3
    # round 2+ context carries the progress log
    ctx2 = azure.translate_step.call_args_list[1].kwargs["app_context"]
    assert "PROGRESS" in ctx2 and "#menu1" in ctx2
    ctx3 = azure.translate_step.call_args_list[2].kwargs["app_context"]
    assert "#menu2" in ctx3
    # both executed clicks live in the tape detail
    assert "#menu1" in step.detail and "#menu2" in step.detail
    # evaluator saw the nav frames + the final frame
    frames, _expected = azure.evaluate_result.call_args.args
    assert len(frames) == 3


@pytest.mark.asyncio
async def test_act_observe_loop_is_round_capped():
    """A model that navigates forever must hit the round cap, then evaluate."""
    nav_click = [{"action": "click", "selector": "#next", "value": None}]
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Tour everything", "expected": "All good"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[nav_click] * 10,
        evaluate_side_effect=[{"status": "blocked", "reason": "ran out of budget"}],
    )
    browser = _fake_browser()
    browser.current_url = AsyncMock(side_effect=[f"u{i}" for i in range(100)])
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
        step_attempts=1,  # single-shot: isolates the round cap from step retries
    )
    state = await orch.run_single_case("A")
    assert azure.translate_step.await_count == 6  # MAX_ROUNDS
    assert azure.evaluate_result.await_count == 1
    assert state.test_cases[0].steps[0].status == "blocked"


# ----- PERFORMED ACTIONS reach the evaluator ---------------------------------


@pytest.mark.asyncio
async def test_evaluate_result_receives_performed_actions_summary():
    """The evaluator must be told what the agent actually did, so findings can
    state HOW the outcome was achieved."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
    ]}]
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
    await orch.run_single_case("A")
    kwargs = azure.evaluate_result.call_args.kwargs
    assert "performed" in kwargs
    assert "click" in kwargs["performed"]
    assert "#go" in kwargs["performed"]


def test_format_detail_renders_login_logout_as_bare_action_names():
    """The performed-actions summary must never leak credentials — login and
    logout carry no selector/value, so _format_detail renders them bare."""
    from agent.orchestrator import _format_detail

    detail = _format_detail([{"action": "login", "ref": None, "selector": None, "value": None}])
    assert detail == "login"
    detail = _format_detail([{"action": "logout", "ref": None, "selector": None, "value": None}])
    assert detail == "logout"


# ----- tester-guidance injection (override-as-knowledge) --------------------


@pytest.mark.asyncio
async def test_evaluator_receives_guidance_from_lookup_with_original_index():
    """The evaluator must receive whatever lookup_guidance() returns, and the
    lookup must be keyed on the ORIGINAL step index (not the tape index) —
    verified by running only step_indices=[1] of a 2-step case, so tape
    index 0 != original index 1."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Step zero", "expected": "E0"},
        {"action": "Step one", "expected": "E1"},
    ]}]
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
        step_attempts=1,  # single-shot: not testing retries here
    )
    with patch(
        "agent.orchestrator.lookup_guidance", return_value="- tester overrode 'fail' to 'pass': flaky banner"
    ) as mock_lookup:
        state = await orch.run_single_case("A", step_indices=[1])

    mock_lookup.assert_called_once_with("A", 1, "Step one")
    kwargs = azure.evaluate_result.call_args.kwargs
    assert kwargs["guidance"] == "- tester overrode 'fail' to 'pass': flaky banner"
    assert state.test_cases[0].steps[0].status == "pass"


@pytest.mark.asyncio
async def test_guidance_lookup_exception_does_not_fail_step():
    """A knowledge-lookup problem must never affect the run — it degrades to
    empty guidance and the step still evaluates normally."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Step zero", "expected": "E0"},
    ]}]
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
        step_attempts=1,  # single-shot: not testing retries here
    )
    with patch(
        "agent.orchestrator.lookup_guidance", side_effect=RuntimeError("knowledge file exploded")
    ):
        state = await orch.run_single_case("A")

    kwargs = azure.evaluate_result.call_args.kwargs
    assert kwargs["guidance"] == ""
    assert state.test_cases[0].steps[0].status == "pass"


# ----- step retries + escalation (2026-07-09) --------------------------------


@pytest.mark.asyncio
async def test_step_retries_on_fail_and_passes_on_third_attempt():
    """fail, fail, pass -> the step resolves pass. Attempts 2-3 carry an
    escalating-exploration prefix in the translator context naming the
    attempt number and the previous attempt's verdict/reason. resolve_step
    fires exactly once (the pass overwrites nothing — verified by the final
    evaluation text matching the LAST evaluate call, not an earlier one)."""
    from agent.run_state import RunState

    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions(), _ok_actions(), _ok_actions()],
        evaluate_side_effect=[
            {"status": "fail", "reason": "no dialog"},
            {"status": "fail", "reason": "still no dialog"},
            {"status": "pass", "reason": "Loaded"},
        ],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )

    original_resolve_step = RunState.resolve_step
    resolve_calls: list = []

    def _spy_resolve_step(self, *args, **kwargs):
        resolve_calls.append((args, kwargs))
        return original_resolve_step(self, *args, **kwargs)

    with patch.object(RunState, "resolve_step", _spy_resolve_step):
        state = await orch.run_single_case("A")

    step = state.test_cases[0].steps[0]
    assert step.status == "pass"
    assert step.evaluation == "Loaded"
    assert len(resolve_calls) == 1  # resolve_step fired exactly once for the step

    assert azure.translate_step.await_count == 3
    assert azure.evaluate_result.await_count == 3
    ctx2 = azure.translate_step.call_args_list[1].kwargs["app_context"]
    assert "ATTEMPT 2 of 3" in ctx2 and "no dialog" in ctx2
    ctx3 = azure.translate_step.call_args_list[2].kwargs["app_context"]
    assert "ATTEMPT 3 of 3" in ctx3 and "still no dialog" in ctx3


@pytest.mark.asyncio
async def test_step_exhausts_all_attempts_and_escalates_to_human_review():
    """always-fail -> exactly 3 attempts, final evaluation ends with the
    NEEDS HUMAN REVIEW suffix, step status stays fail, and the CASE
    CONTINUES to later steps rather than stopping."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
        {"action": "Click again", "expected": "Done"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions()] * 4,
        evaluate_side_effect=[
            {"status": "fail", "reason": "no dialog"},
            {"status": "fail", "reason": "still no dialog"},
            {"status": "fail", "reason": "final no dialog"},
            {"status": "pass", "reason": "Done"},
        ],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")
    case = state.test_cases[0]
    step1, step2 = case.steps

    assert step1.status == "fail"
    assert step1.evaluation.endswith("NEEDS HUMAN REVIEW (3 agent attempts)")
    assert "final no dialog" in step1.evaluation
    assert step2.status == "pass"  # case continued past the exhausted step
    assert case.status == "fail"
    assert azure.translate_step.await_count == 4
    assert azure.evaluate_result.await_count == 4


@pytest.mark.asyncio
async def test_step_passing_on_first_attempt_is_unchanged_behavior():
    """pass on attempt 1 -> exactly one attempt; no escalation context is
    added, and the evaluation carries no retry-related suffix."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions()],
        evaluate_side_effect=[{"status": "pass", "reason": "Loaded"}],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")
    step = state.test_cases[0].steps[0]
    assert step.status == "pass"
    assert step.evaluation == "Loaded"  # no suffix appended
    assert azure.translate_step.await_count == 1
    assert azure.evaluate_result.await_count == 1
    ctx = azure.translate_step.call_args_list[0].kwargs["app_context"]
    assert "ATTEMPT" not in ctx


@pytest.mark.asyncio
async def test_step_attempts_one_restores_old_single_shot_behavior():
    """Orchestrator(step_attempts=1) never retries — a single fail/blocked
    evaluation resolves the step immediately, exactly like the pre-retry
    behavior."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions()],
        evaluate_side_effect=[{"status": "fail", "reason": "no dialog"}],
    )
    browser = _fake_browser()
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
        step_attempts=1,
    )
    state = await orch.run_single_case("A")
    step = state.test_cases[0].steps[0]
    assert step.status == "fail"
    assert azure.translate_step.await_count == 1
    assert azure.evaluate_result.await_count == 1
