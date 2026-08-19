"""The main agent loop.

For each test case in a plan:
  1. fetch detail (CaseSource — fixture today, QMetry later)
  2. translate each step into Playwright actions (Azure AI)
  3. open a fresh browser session
  4. execute each translated action
  5. screenshot
  6. evaluate the screenshot against the step's expected result (Azure vision)
  7. resolve the step in run_state — frontend sees this on the next poll
  8. close browser
  9. if FAIL and AUTO_CREATE_BUGS: create a Jira bug (deferred)

Catches every per-case exception so one bad case never kills the run.
Calls `on_update(state)` after every observable transition so the frontend
tape updates live.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable

from agent.azure_ai import AzureAIClient, AzureAIError
from agent.browser import BrowserError, BrowserSession
from agent.case_source import CaseSource, FixtureCaseSource
from agent.knowledge import lookup_guidance
from agent.login import login
from agent.run_state import RunState, Step, TestCase, new_run_state

log = logging.getLogger(__name__)

# Type aliases
OnUpdate = Callable[[RunState], None]
BrowserFactory = Callable[[], BrowserSession]

# Phrases that flag a step as needing a value that must ALREADY EXIST in the
# app (a duplicate email, an in-use name, an already-registered serial) for a
# negative-path assertion to mean anything. TC-2915 ("Verify Cannot Edit
# Email Address to One That Already Exists") has empty QMetry test_data, so
# without this the model invented an email nothing else in the system had —
# the app accepted the "duplicate" and the negative test verified nothing.
# When a step's action/expected text matches one of these (case-insensitive),
# and the run is live, the translator context gets a PAGE DATA block of real
# on-page table values (see BrowserSession.snapshot_table_data). Extend this
# tuple as new phrasings turn up in QMetry steps.
_EXISTING_DATA_PHRASES = (
    "already exists",
    "already assigned",
    "already in use",
    "already taken",
    "already registered",
    "duplicate",
    "another user",
    "existing user",
    "an existing",
    "that already",
)


def _step_needs_existing_data(action_text: str, expected: str) -> bool:
    """True if this step's text implies it needs a real, pre-existing value.

    Matches `_EXISTING_DATA_PHRASES` case-insensitively against the step's
    action and expected-result text combined.
    """
    haystack = f"{action_text} {expected}".lower()
    return any(phrase in haystack for phrase in _EXISTING_DATA_PHRASES)


_PAGE_DATA_HEADER_CURRENT = (
    "PAGE DATA — real values currently visible on this page in the "
    "application (not invented). Use these for any value that must "
    "already exist (e.g. a duplicate email); never fabricate a "
    "placeholder like example.com or test@test.com."
)


def _page_data_header_remembered(url: str) -> str:
    """Header for a PAGE DATA block built from a table seen earlier in the
    case rather than the current page — the model must know the values are
    real but not currently on screen, and where they came from."""
    return (
        f"PAGE DATA — real values seen earlier in this case on {url} "
        "(not currently on screen, but real — not invented). Use these for "
        "any value that must already exist (e.g. a duplicate email); never "
        "fabricate a placeholder like example.com or test@test.com."
    )


def _format_page_data_lines(header: str, table: dict) -> str:
    lines = [header]
    headers = table.get("headers") or []
    if headers:
        lines.append(" | ".join(headers))
    for row in table.get("rows") or []:
        lines.append(" | ".join(row))
    return "\n".join(lines)


class _TableMemory:
    """Remembers the most recent non-empty on-page table seen during ONE case.

    A fresh instance is created in `_execute_case` for every case and passed
    down as a plain argument — it is never stored on `self` — so values seen
    in one case (e.g. a Users list's real emails) can never leak into the
    next case's translator context. Updated by two callers that both funnel
    through `remember()` so "most recent non-empty" is a single rule: the
    opportunistic post-navigation capture in `_attempt_step`, and the
    PAGE DATA block builder's own current-page fetch in
    `_build_page_data_block`.
    """

    def __init__(self) -> None:
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self.url: str = ""

    def remember(self, table: dict, url: str) -> None:
        rows = table.get("rows") or []
        if rows:
            self.headers = table.get("headers") or []
            self.rows = rows
            self.url = url

    @property
    def has_data(self) -> bool:
        return bool(self.rows)


class Orchestrator:
    def __init__(
        self,
        azure: AzureAIClient | None = None,
        browser_factory: BrowserFactory | None = None,
        case_source: CaseSource | None = None,
        jira=None,
        on_update: OnUpdate | None = None,
        step_attempts: int | None = None,
        launch_delay_s: float | None = None,
        eval_max_frames: int | None = None,
    ):
        self.azure = azure or AzureAIClient()
        self.browser_factory = browser_factory or BrowserSession
        self.case_source = case_source or FixtureCaseSource()
        self.jira = jira
        self.on_update = on_update or (lambda _state: None)
        self.auto_create_bugs = (
            os.environ.get("AUTO_CREATE_BUGS", "false").lower() == "true"
        )
        # Step retry/escalation (2026-07-09): every agent-executed step gets up
        # to `step_attempts` fresh act->observe+evaluate passes before the
        # step's final (non-pass) status is escalated for human review.
        self.step_attempts = (
            step_attempts if step_attempts is not None
            else int(os.environ.get("STEP_MAX_ATTEMPTS", "3"))
        )
        self.step_attempt_budget_s = float(os.environ.get("STEP_ATTEMPT_BUDGET_S", "150"))
        # Pause between a case's browser appearing and its first action, so a
        # human watching a visible window can follow along. Pointless when
        # headless, where it would only add wall clock (3s × 73 cases ≈ 3.5min),
        # so _execute_case skips it there.
        self.launch_delay_s = (
            launch_delay_s if launch_delay_s is not None
            else float(os.environ.get("AGENT_LAUNCH_DELAY_S", "3"))
        )
        # How many of a step's captured frames reach the evaluator. Base64 PNGs
        # dominate evaluation token cost, so this is the largest cost lever in a
        # run — larger than the model tier. The final frame is always included:
        # the window keeps the LAST n frames, so a value of 1 still evaluates
        # the settled end state. Values below 1 are clamped to 1.
        raw_frames = (
            eval_max_frames if eval_max_frames is not None
            else int(os.environ.get("EVAL_MAX_FRAMES", "8"))
        )
        self.eval_max_frames = max(1, raw_frames)

    # ------------------------------------------------------------------ public

    async def run_plan(
        self,
        plan_key: str,
        credentials: tuple[str, str] | None = None,
        case_credentials: dict[str, tuple[str, str]] | None = None,
        target_url: str | None = None,
    ) -> RunState:
        """Run an entire plan end-to-end. Returns the final RunState.

        `credentials` is the run-level (username, password) override; a case id
        present in `case_credentials` uses that pair instead. None means the
        .env account. Credentials never enter run_state, a prompt, or a log
        line — they reach BrowserSession.credentials and nowhere else. The
        per-case map is built by the caller (server.py reads ManualStore) so
        this module stays independent of the manual session store.

        `target_url` is the GLOBAL server override (the console-wide setting
        in `agent/settings.py`) — the SAME value applies to every case in the
        plan; there is no per-case override any more. None/empty means the
        browser factory's default (APP_BASE_URL). Not a secret: unlike
        credentials, it may be logged.
        """
        plan = await self.case_source.get_plan(plan_key)
        state = new_run_state(plan["key"], plan["name"])

        try:
            cases = await self.case_source.list_cases(plan_key)
        except Exception as e:
            log.exception("Could not load plan %s", plan_key)
            state.start_run()
            self.on_update(state)
            state.finish()
            self.on_update(state)
            raise

        # Pre-populate the rail so the tester can see what's coming. Precondition
        # and case test data ride along from the case list — no extra QMetry call.
        for c in cases:
            state.add_case(
                TestCase(
                    id=c["id"],
                    name=c["name"],
                    precondition=c.get("precondition") or None,
                    test_data=list(c.get("test_data") or []),
                )
            )

        state.start_run()
        self.on_update(state)

        per_case = case_credentials or {}
        for case in cases:
            try:
                # `or credentials`, not membership: the only producer
                # (server._manual_case_credentials) ever inserts complete
                # pairs, so a present-but-falsy value here means the tester
                # deliberately left the field blank ("nothing typed"), and
                # falls through to the run-level pair, then to the .env
                # account — the same rule the docstring above describes.
                await self._execute_case(
                    state, case,
                    credentials=per_case.get(case["id"]) or credentials,
                    target_url=target_url,
                )
            except Exception:
                log.exception("Case %s crashed; marking blocked", case.get("id"))
                state.resolve_case(case["id"], "blocked")
                self.on_update(state)

        state.finish()
        self.on_update(state)
        return state

    async def run_single_case(
        self,
        case_id: str,
        plan_key: str = "",
        dry_run: bool = False,
        step_indices: list[int] | None = None,
        credentials: tuple[str, str] | None = None,
        target_url: str | None = None,
    ) -> RunState:
        """Run one case (used by `main.py --testcase` and the Manual tab).

        `step_indices` (0-based, original step positions) limits execution to
        those steps; None runs all. The run-state tape contains only the
        executed steps.

        `credentials`, if given, overrides the .env login account for this
        case only (Manual-tab per-case credentials). Never logged, never put
        in any prompt/context string or run_state — it only reaches
        `BrowserSession.credentials`, which `login()` reads directly.

        `target_url`, if given, overrides the .env server for this run
        (the console-wide global setting in `agent/settings.py` — server.py
        passes the same value into every plan run and every Manual single-case
        run; there is no per-case override any more). Not a secret — it may
        be logged.
        """
        # Steps-less list, then hydrate just this case: fetching every case's
        # steps to run one of them costs a QMetry call per case in the cycle.
        cases = await self.case_source.list_cases(plan_key, with_steps=False)
        match = next((c for c in cases if c["id"] == case_id), None)
        if match is None:
            raise KeyError(f"No fixture case with id {case_id!r}")
        if not match.get("_steps_loaded"):
            match["steps"] = await self.case_source.get_case_steps(plan_key, case_id)
        plan = await self.case_source.get_plan(plan_key)
        state = new_run_state(plan["key"], plan["name"])
        state.add_case(
            TestCase(
                id=match["id"],
                name=match["name"],
                precondition=match.get("precondition") or None,
                test_data=list(match.get("test_data") or []),
            )
        )
        state.start_run()
        self.on_update(state)
        try:
            await self._execute_case(
                state, match, dry_run=dry_run, step_indices=step_indices,
                credentials=credentials, target_url=target_url,
            )
        finally:
            state.finish()
            self.on_update(state)
        return state

    # ----------------------------------------------------------------- private

    async def _execute_case(
        self,
        state: RunState,
        case: dict[str, Any],
        dry_run: bool = False,
        step_indices: list[int] | None = None,
        credentials: tuple[str, str] | None = None,
        target_url: str | None = None,
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

        browser = None
        if not dry_run:
            browser = self.browser_factory()
            browser.credentials = credentials
            if target_url:
                browser.base_url = target_url.rstrip("/")
                log.info("Case %s: server override %s", case_id, browser.base_url)
            try:
                await browser.open_session()
                if self.launch_delay_s > 0 and not getattr(browser, "headless", True):
                    log.info(
                        "Launch delay: %.1fs before the first action of %s",
                        self.launch_delay_s, case_id,
                    )
                    await asyncio.sleep(self.launch_delay_s)
                await login(browser)
            except BrowserError as e:
                log.error("Login failed for case %s: %s", case_id, e)
                state.resolve_case(case_id, "blocked")
                self.on_update(state)
                return
            except Exception:
                log.exception("Could not open browser for case %s", case_id)
                state.resolve_case(case_id, "blocked")
                self.on_update(state)
                return

        # Whole-case brief so the translator understands the flow, not just the
        # sentence in front of it. Rebuilt each step to carry outcomes so far.
        selected_set = {i for i, _ in selected}
        step_status: dict[int, str] = {}

        def _case_brief(current: int) -> str:
            lines = [f"TEST CASE: {case_id} — {case.get('name', case_id)}", "Steps:"]
            for i, s in enumerate(steps):
                text = " ".join(str(s.get("action", "")).split())[:120]
                if i == current:
                    marker = ">> CURRENT — execute ONLY this step now"
                elif i in step_status:
                    marker = f"done: {step_status[i]}"
                elif i in selected_set:
                    marker = "upcoming (do not do it yet)"
                else:
                    marker = "MANUAL — the tester does this one by hand; skip it"
                lines.append(f"  {i + 1}. {text}  [{marker}]")
            return "\n".join(lines)

        # Per-case PAGE DATA memory (2026-08-13 carry-forward fix): a page a
        # test navigates away from (e.g. a Users list) may hold the only real
        # values a later step needs (e.g. a duplicate email on the Edit User
        # page). Fresh per case — see `_TableMemory` docstring for why it is
        # never stored on `self`.
        table_memory = _TableMemory()

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
                    case_context=_case_brief(orig_index), orig_index=orig_index,
                    table_memory=table_memory,
                )
                step_status[orig_index] = step_outcome
                if step_outcome == "fail":
                    outcome = "fail"
                elif step_outcome == "blocked" and outcome != "fail":
                    outcome = "blocked"
        finally:
            if browser is not None:
                try:
                    await browser.close_session()
                except Exception:
                    log.warning("Failed to close browser cleanly for case %s", case_id)

        state.resolve_case(case_id, outcome)
        self.on_update(state)

        if outcome == "fail" and self.auto_create_bugs and self.jira is not None:
            try:
                await self._create_bug(state, case_id)
            except Exception:
                log.exception("Failed to create Jira bug for %s", case_id)

    async def _execute_step(
        self,
        state: RunState,
        case_id: str,
        step_index: int,
        step: dict[str, Any],
        browser: BrowserSession | None,
        dry_run: bool = False,
        case_context: str = "",
        orig_index: int | None = None,
        table_memory: "_TableMemory | None" = None,
    ) -> str:
        """Run one step. Returns 'pass' | 'fail' | 'blocked'.

        Live mode: snapshot the page, translate against its real elements, execute.
        On a BrowserError, re-snapshot + re-translate (telling the model what failed)
        and retry the step ONCE before marking it FAIL — that heal-retry happens
        *within* a single attempt's act->observe loop (`_attempt_step`).

        On top of that, the whole attempt (act->observe loop + screenshot/evaluate)
        runs up to `self.step_attempts` times (2026-07-09 retry-escalation spec).
        Attempts after the first tell the translator what the previous attempt's
        verdict was and push it to interact with the specific controls the step
        names rather than stop at the page. `run_state.resolve_step` fires exactly
        once for the step — on a pass, or on the final attempt — every attempt in
        between only updates `rs_step.detail` live. If the final attempt still
        isn't a pass, its stored evaluation gains a "NEEDS HUMAN REVIEW" suffix.

        `orig_index` is the step's ORIGINAL position in the case (differs from
        `step_index`/tape position when `step_indices` filters the run) — it's
        the key used to look up tester-guidance knowledge for this exact step.
        `step_index` keeps indexing the run_state tape unchanged.

        `table_memory` is the case's `_TableMemory` (see `_execute_case`) —
        threaded through so a step can both use and refresh it.
        """
        if orig_index is None:
            orig_index = step_index
        # The model needs the step's test data folded into the instruction (those
        # are the values it must type). The console keeps them apart so it can
        # label them per step — hence the re-join here rather than in the source.
        action_text = step["action"]
        if step.get("test_data"):
            action_text = f"{action_text}\nTest data: {step['test_data']}"
        expected = step.get("expected", "")

        # The model gets action + test data joined (action_text); the tape keeps
        # them apart so the console can label test data per step, exactly as the
        # Manual panel does.
        rs_step = Step(
            action=step["action"],
            detail="translating…",
            status="running",
            test_data=step.get("test_data") or None,
        )
        state.add_step(case_id, rs_step)
        self.on_update(state)
        start = time.monotonic()

        # --- dry-run: translate only, no browser -----------------------------
        if dry_run:
            try:
                dry_ctx = f"{case_context}\ndry-run mode" if case_context else "dry-run mode"
                actions = await self.azure.translate_step(action_text, app_context=dry_ctx)
            except AzureAIError as e:
                duration = time.monotonic() - start
                state.resolve_step(case_id, step_index, "blocked",
                                   f"Could not translate step: {e}", duration)
                self.on_update(state)
                return "blocked"
            rs_step.detail = _format_detail(actions)
            duration = time.monotonic() - start
            state.resolve_step(case_id, step_index, "pass",
                               "[dry-run] translation OK — browser execution skipped", duration)
            self.on_update(state)
            return "pass"

        # --- live: attempt loop -------------------------------------------------
        attempts_max = self.step_attempts
        prev_verdict: tuple[str, str] | None = None

        for attempt in range(1, attempts_max + 1):
            escalation = ""
            if prev_verdict is not None:
                prev_status, prev_reason = prev_verdict
                escalation = (
                    f"ATTEMPT {attempt} of {attempts_max} — the previous attempt "
                    f"was judged {prev_status}: {prev_reason}. Do not stop at the "
                    "page the step names: interact with the specific CONTROLS it "
                    "names in the PLACE it names them (row action icons like the "
                    "pencil, buttons inside panels, checkboxes in edit forms) "
                    "before concluding."
                )

            status, reason, png_b64, execution_ok = await self._attempt_step(
                state, rs_step, case_id, orig_index, action_text, expected, browser,
                case_context=case_context, escalation=escalation,
                table_memory=table_memory,
            )

            # A non-pass verdict on a CLEAN execution is the evaluator's
            # judgement of the app, not a failure to drive it. Retrying cannot
            # change that judgement, and re-running a committing action (Save,
            # Delete, Submit) would mutate the system under test again — which
            # has already cost this project one account setting. So spend
            # further attempts only when something actually went wrong.
            verdict_is_final = status != "pass" and execution_ok
            if verdict_is_final and attempt < attempts_max:
                log.info(
                    "Step %s/%s judged %s on a clean execution — not retrying "
                    "(%d of %d attempts used)",
                    case_id, orig_index, status, attempt, attempts_max,
                )

            is_last = attempt == attempts_max
            if status == "pass" or is_last or verdict_is_final:
                final_reason = reason
                if status != "pass":
                    final_reason = (
                        f"{reason} — NEEDS HUMAN REVIEW ({attempt} agent attempts)"
                    )
                duration = time.monotonic() - start
                state.resolve_step(case_id, step_index, status, final_reason, duration,
                                   screenshot_b64=png_b64)
                self.on_update(state)
                return status

            # Not a pass, and more attempts remain — surface progress, then retry.
            rs_step.detail = f"attempt {attempt}/{attempts_max}: {rs_step.detail}"
            self.on_update(state)
            prev_verdict = (status, reason)

        # Unreachable: attempts_max >= 1 always returns from inside the loop above.
        raise AssertionError("step attempt loop exited without resolving the step")

    async def _attempt_step(
        self,
        state: RunState,
        rs_step: Step,
        case_id: str,
        orig_index: int,
        action_text: str,
        expected: str,
        browser: BrowserSession,
        case_context: str,
        escalation: str,
        table_memory: "_TableMemory | None" = None,
    ) -> tuple[str, str, str | None, bool]:
        """Run ONE attempt: the act -> observe loop, then screenshot + evaluate.

        Returns `(status, reason, screenshot_b64, execution_ok)`. `status` is
        one of 'pass' | 'fail' | 'blocked'. `screenshot_b64` is the final
        frame when evaluation was reached, else None (translate failure or an
        action failure with nothing yet executed — same "no evidence" cases
        the old single-attempt code returned immediately on). `execution_ok`
        is a placeholder `True` for now (wired up by the retry-scoping fix in
        the next commit).

        A navigation stales every ref from the snapshot, so after any action
        that changes the page URL we re-observe (fresh snapshot) and let the
        model plan the remainder with a PROGRESS log. Single-page plans keep
        the old fast path: one round, no extra model calls. The model stops
        the loop by returning {"actions": [], "done": true}.

        Time budget: checked between rounds only (never interrupts a round in
        flight) — if this attempt has run longer than `step_attempt_budget_s`
        AND the previous round executed no actions, the loop stops and goes
        to evaluation on whatever evidence exists so far.
        """
        max_rounds = 6
        max_actions = 20
        frames: list[str] = []
        executed_actions: list[dict[str, Any]] = []
        last_error: str | None = None
        consecutive_error_rounds = 0
        attempt_start = time.monotonic()
        prev_round_had_actions = True  # no "previous round" yet — round 0 always runs

        # PAGE DATA (2026-08-13): only for steps whose text implies they need
        # a value that must already exist in the app. Fetched at most once per
        # attempt (cached below) and appended to `context` further down — for
        # every other step this whole block is inert and `context` is built
        # exactly as before.
        needs_page_data = _step_needs_existing_data(action_text, expected)
        page_data_block: str | None = None

        for _round in range(max_rounds):
            if _round > 0 and not prev_round_had_actions:
                if (time.monotonic() - attempt_start) > self.step_attempt_budget_s:
                    break  # budget exhausted and nothing is "ongoing" — judge now

            actions_before = len(executed_actions)

            try:
                elements = await browser.snapshot_elements()
            except Exception:
                elements = []

            context = f"current URL: {await browser.current_url()}"
            if expected:
                context = (
                    "CURRENT step EXPECTED RESULT (for RECONCILE-FIRST state "
                    "checks; verification itself happens later from "
                    "screenshots):\n"
                    f"{expected}\n{context}"
                )
            if case_context:
                context = f"{case_context}\n{context}"
            if executed_actions:
                progress = "\n".join(
                    f"  {i + 1}. {_format_detail([a])}"
                    for i, a in enumerate(executed_actions)
                )
                context += (
                    "\nPROGRESS — actions already performed for the CURRENT step"
                    " (possibly on earlier pages):\n" + progress
                )
            if last_error:
                context += (
                    f"\nPrevious action failed: {last_error}. "
                    "Pick a different element or approach."
                )
            if escalation:
                context = f"{escalation}\n{context}"
            if needs_page_data:
                if page_data_block is None:
                    page_data_block = await self._build_page_data_block(
                        browser, table_memory
                    )
                if page_data_block:
                    context += f"\n{page_data_block}"

            try:
                actions = await self.azure.translate_step(
                    action_text, app_context=context, elements=elements
                )
            except AzureAIError as e:
                if executed_actions:
                    break  # judge on the evidence gathered so far
                # Translation never happened — an execution problem, so a
                # retry has something new to try.
                return "blocked", f"Could not translate step: {e}", None, False

            if not actions:
                break  # model signals the step's goal is complete

            rs_step.detail = _format_detail(executed_actions + actions)
            self.on_update(state)

            last_error = None
            navigated = False
            for i, a in enumerate(actions):
                if len(executed_actions) >= max_actions:
                    break
                url_before = await browser.current_url()
                try:
                    await browser.execute_action(a)
                except BrowserError as e:
                    last_error = str(e)
                    break
                executed_actions.append(a)
                rs_step.detail = _format_detail(executed_actions)
                self.on_update(state)
                try:
                    navigated = (await browser.current_url()) != url_before
                except Exception:
                    navigated = False
                # Capture the transient state after every action except a
                # same-page plan's last one — the settled final screenshot
                # below covers the step's end state.
                if navigated or i < len(actions) - 1:
                    try:
                        await browser.wait_for_settle(quiet_ms=400, timeout_ms=3_000)
                        frames.append(await browser.screenshot())
                    except Exception:
                        pass  # a lost frame never fails the step
                if navigated:
                    # A page we just navigated to may hold the only real
                    # values a LATER step in this case needs (carry-forward
                    # fix, 2026-08-13) — opportunistic, cheap, no model
                    # tokens; never affects this step on failure.
                    await self._capture_table_opportunistically(browser, table_memory)
                    break  # refs are stale — re-observe the new page

            prev_round_had_actions = len(executed_actions) > actions_before

            if last_error is not None:
                consecutive_error_rounds += 1
                if consecutive_error_rounds >= 2:
                    if not executed_actions:
                        # Nothing ran: the agent could not act at all, which is
                        # exactly what a retry exists for.
                        return "fail", last_error, None, False
                    break  # persistent errors — judge on the evidence gathered
                continue  # re-observe with the error in context
            consecutive_error_rounds = 0

            if len(executed_actions) >= max_actions:
                break
            if not navigated:
                break  # whole plan ran on one page — step complete (fast path)

        # --- screenshot + evaluate --------------------------------------------
        try:
            # Let animations/network settle so the final frame shows the final UI.
            await browser.wait_for_settle()
            png_b64 = await browser.screenshot()
            frames.append(png_b64)
            # A knowledge-lookup problem must never affect a run — degrade to
            # no guidance rather than fail the step.
            try:
                guidance = lookup_guidance(case_id, orig_index, action_text)
            except Exception:
                log.warning("lookup_guidance failed for %s/%s", case_id, orig_index, exc_info=True)
                guidance = ""
            # Cap what we send the evaluator; always keep the final frame.
            # Images dominate evaluation cost, so this window is the biggest
            # cost lever in a run — see EVAL_MAX_FRAMES.
            evaluation = await self.azure.evaluate_result(
                frames[-self.eval_max_frames:], expected,
                performed=_format_detail(executed_actions),
                step_text=action_text,
                guidance=guidance,
            )
        except (BrowserError, AzureAIError) as e:
            # Screenshot or evaluator call blew up — no verdict was reached, so
            # a retry is worth spending.
            return "fail", f"Could not evaluate result: {e}", None, False

        status = evaluation["status"]
        if status not in ("pass", "fail", "blocked"):
            status = "fail"
        # Clean execution: the actions ran and the EVALUATOR produced this
        # verdict. Retrying cannot change a verdict about the app's behaviour.
        return status, evaluation["reason"], png_b64, True

    async def _capture_table_opportunistically(
        self, browser: BrowserSession, table_memory: "_TableMemory | None"
    ) -> None:
        """After a navigation, opportunistically remember this page's table.

        Cheap DOM query, no model tokens. A step that never triggers PAGE
        DATA itself (e.g. the "click Users" step that lands on the Users
        list) may still be the ONLY point in the case where a later,
        triggering step's needed values are on screen — this is what makes
        that value available then. Silently does nothing on failure, an
        empty page, or a missing `table_memory` — a snapshot problem must
        never affect the step. Only the row count is ever logged; table
        content (e.g. real emails) never reaches a log line.
        """
        if table_memory is None:
            return
        try:
            table = await browser.snapshot_table_data()
        except Exception:
            log.warning("snapshot_table_data failed during opportunistic capture", exc_info=True)
            return
        rows = table.get("rows") or []
        if not rows:
            return
        try:
            url = await browser.current_url()
        except Exception:
            url = ""
        table_memory.remember(table, url)
        log.info(
            "Remembered on-page table for later PAGE DATA fallback: %d row(s) at %s",
            len(rows), url,
        )

    async def _build_page_data_block(
        self, browser: BrowserSession, table_memory: "_TableMemory | None" = None
    ) -> str:
        """Build a PAGE DATA block, preferring the current page's table.

        Falls back to `table_memory` (the case's most recent non-empty table
        seen on an earlier page) when the current page has none — the value
        a negative-path step needs (e.g. a duplicate email) is often on a
        list page the case navigated away from before this step ran. Returns
        "" if neither source has anything (never raises — a snapshot problem
        must not affect the step). The block may contain real values such as
        user emails, which is fine for a model prompt, but it must NEVER be
        written to a log line, run_state, or an SSE event — only row counts
        and URLs are logged.
        """
        try:
            table = await browser.snapshot_table_data()
        except Exception:
            log.warning("snapshot_table_data failed", exc_info=True)
            table = {"headers": [], "rows": []}

        rows = table.get("rows") or []
        if rows:
            if table_memory is not None:
                try:
                    url = await browser.current_url()
                except Exception:
                    url = ""
                table_memory.remember(table, url)
            log.info(
                "Attaching PAGE DATA block to translator context: %d row(s) "
                "from the current page", len(rows),
            )
            return _format_page_data_lines(_PAGE_DATA_HEADER_CURRENT, table)

        if table_memory is not None and table_memory.has_data:
            log.info(
                "Attaching PAGE DATA block to translator context: %d row(s) "
                "remembered from earlier in this case (captured on %s)",
                len(table_memory.rows), table_memory.url,
            )
            return _format_page_data_lines(
                _page_data_header_remembered(table_memory.url),
                {"headers": table_memory.headers, "rows": table_memory.rows},
            )

        return ""

    async def _create_bug(self, state: RunState, case_id: str) -> None:
        """Stub — wire up once jira_client is implemented."""
        log.info("AUTO_CREATE_BUGS=true but jira_client is not wired yet")


def _format_detail(actions: list[dict[str, Any]]) -> str:
    """Join translated actions into a one-line mono detail string."""
    parts: list[str] = []
    for a in actions:
        act = a.get("action", "?")
        sel = a.get("selector") or ""
        val = a.get("value")
        if val:
            parts.append(f"{act} {sel} {val!r}".strip())
        else:
            parts.append(f"{act} {sel}".strip())
    return "; ".join(parts)
