"""FastAPI server — what the frontend talks to.

Endpoints (see FRONTEND.md):
  POST /runs                 -> {"run_id": ...}    start a run in the background
  GET  /runs/{id}            -> run_state JSON     current state (Mode A polling)
  GET  /runs/{id}/stream     -> SSE                push step/status events (Mode B)
  POST /runs/{id}/report     -> {"path": ...}      generate HTML report
  POST /runs/{id}/log-bugs   -> {"created": [...]} gated: only on done + has failures
  POST /runs/{id}/cancel     -> {"cancelled": true} cancel a running background task
  GET  /config                    -> non-secret bootstrap, incl. the global target_url
  POST /settings/target-url  -> {"target_url": ...} set the GLOBAL server override
                                 for every run (persisted to settings.json)

In production, also serves frontend/dist as static files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent.case_source import CaseSource, FixtureCaseSource
from agent.knowledge import record_override
from agent.manual_state import ManualStore, compose_agent_note, compose_comment
from agent.orchestrator import Orchestrator
from agent.run_state import RunState, TestCase
from agent.settings import SettingsStore

# On Windows, the default uvicorn event loop (SelectorEventLoop) cannot spawn
# subprocesses, so Playwright's browser launch fails with NotImplementedError and
# every agent run blocks at "open browser". The ProactorEventLoop can spawn
# subprocesses. Set the policy at MODULE LEVEL (not under __main__) so it also
# applies to the worker the --reload supervisor imports. No-op off Windows.
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

load_dotenv()

# Duke's corporate network does TLS inspection; the inspecting root CA lives in
# the OS (Windows) trust store, not in certifi. truststore makes Python's ssl use
# the OS store so httpx calls to QMetry / Jira / Azure verify instead of failing
# with CERTIFICATE_VERIFY_FAILED. Best-effort: skip cleanly if unavailable.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover - environment-dependent
    pass

log = logging.getLogger(__name__)

# In-memory registry. Single-process; if scaled to multiple workers, move to a store.
RUNS: dict[str, RunState] = {}
TASKS: dict[str, asyncio.Task] = {}
LISTENERS: dict[str, list[asyncio.Queue]] = {}
# Snapshot the latest state-dict per run so a late SSE subscriber sees the current
# state immediately on connect.
LATEST: dict[str, dict] = {}
# run_id -> (username, password) for the lifetime of that run only. Never
# serialized; deleted when the run ends.
RUN_CREDENTIALS: dict[str, tuple[str, str]] = {}

MANUAL = ManualStore()
SETTINGS = SettingsStore()


class StartRunBody(BaseModel):
    plan: str
    # Run-level login override. Inbound only: held in memory for the run and
    # never echoed in a response, snapshot, or SSE event. Both blank = .env admin.
    username: str = ""
    password: str = ""


class MarkBody(BaseModel):
    status: Literal["unmarked", "pass", "fail", "blocked"]
    comment: str = ""
    failed_steps: list[int] = []


class StepMarkBody(BaseModel):
    status: str
    note: str = ""
    agent_status: str | None = None


class RunAgentBody(BaseModel):
    steps: list[int] | None = None


class CredentialsBody(BaseModel):
    username: str = ""
    password: str = ""


class TargetUrlBody(BaseModel):
    url: str = ""


def _validate_target_url(url: str) -> str:
    """"" clears back to the .env APP_BASE_URL default; anything else must
    parse as an http(s) URL with a host, or this raises a 422 HTTPException so
    a typo fails at save time instead of surfacing as a mysterious BLOCKED
    login mid-run."""
    url = url.strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(
                422, f"Invalid target URL {url!r}: must be an http(s) URL with a host"
            )
    return url


class PushBody(BaseModel):
    mode: Literal["edit", "create"] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    yield
    # cancel any still-running tasks on shutdown
    for t in TASKS.values():
        t.cancel()


app = FastAPI(title="QA Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _validation_error_without_credentials(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Same 422 shape as FastAPI's default handler, minus the raw request body.

    Pydantic v2 attaches the offending object as each error's `input`, and the
    default handler serializes it verbatim — so a malformed POST /runs (e.g.
    missing `plan`) would otherwise echo `username`/`password` straight back
    in the response body. Strip `input` from every error entry; status code
    and the `detail` list shape are unchanged so existing clients/tests
    (which only assert on status_code) keep working.
    """
    errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.middleware("http")
async def _no_cache_html(request, call_next):
    """HTML must revalidate on every load — a cached index.html pins the
    browser to a stale JS bundle. Hash-named assets stay cacheable."""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------- run wiring


def _make_on_update(run_id: str):
    """Return a sync callback that fans state updates out to SSE listeners."""

    def on_update(state: RunState) -> None:
        snapshot = state.to_dict()
        LATEST[run_id] = snapshot
        for q in list(LISTENERS.get(run_id, [])):
            try:
                q.put_nowait(snapshot)
            except asyncio.QueueFull:
                # Drop the frame for slow consumers; they'll catch up on the next.
                log.warning("SSE listener queue full for run %s — dropping frame", run_id)

    return on_update


def _qmetry_configured() -> bool:
    key = os.environ.get("QMETRY_API_KEY", "")
    return bool(key) and not key.startswith("REPLACE_WITH")


def _qmetry_execution_mode() -> str:
    """edit (default) writes results into the case's existing execution;
    create makes a fresh execution run in the same cycle each push."""
    mode = os.environ.get("QMETRY_EXECUTION_MODE", "edit").strip().lower()
    return "create" if mode == "create" else "edit"


def _make_qmetry_client():
    """A bare QMetry client for endpoints that aren't case-source shaped."""
    from agent.qmetry import QMetryClient

    return QMetryClient()


def _make_case_source() -> CaseSource:
    """QMetry when keyed, fixtures otherwise — shared by runs and the manual view."""
    if _qmetry_configured():
        from agent.qmetry import QMetryCaseSource

        return QMetryCaseSource()
    return FixtureCaseSource()


def _build_orchestrator(on_update) -> Orchestrator:
    """Construct the orchestrator with environment-driven defaults."""
    return Orchestrator(case_source=_make_case_source(), on_update=on_update)


async def _run_in_background(run_id: str, plan_key: str, state: RunState) -> None:
    """Wrap orch.run_plan so exceptions don't crash the task silently."""
    try:
        orch = _build_orchestrator(_make_on_update(run_id))
        # The orchestrator builds its own RunState. We want it to write into the
        # already-registered state object so RUNS[run_id] stays the same ref.
        # Easiest: have the orchestrator return a fresh state and replace RUNS[run_id].
        final = await orch.run_plan(
            plan_key,
            credentials=RUN_CREDENTIALS.get(run_id),
            case_credentials=_manual_case_credentials(plan_key),
            target_url=SETTINGS.get("target_url") or None,
        )
        RUNS[run_id] = final
    except Exception:
        log.exception("Run %s crashed", run_id)
        # mark blocked so the UI shows something terminal
        state.finish()
        _make_on_update(run_id)(state)
    finally:
        RUN_CREDENTIALS.pop(run_id, None)


def _manual_case_credentials(plan_key: str) -> dict[str, tuple[str, str]]:
    """Per-case logins saved in the Manual tab, which outrank the run-level pair.

    Kept here rather than in the orchestrator so agent/ never imports the
    manual session store.
    """
    session = MANUAL.get(plan_key)
    if session is None:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for case in session.cases:
        mark = case.mark
        if mark.login_username and mark.login_password:
            out[case.id] = (mark.login_username, mark.login_password)
    return out


async def _run_agent_case(
    run_id: str,
    plan: str,
    case_id: str,
    state: RunState,
    step_indices: list[int] | None = None,
) -> None:
    """Run a single case for the manual view; reflect its result on the mark.

    The completion set_agent calls deliberately omit agent_steps — the sentinel
    in ManualStore.set_agent preserves the selection recorded at run start.
    """
    creds = None
    target_url = SETTINGS.get("target_url") or None
    session = MANUAL.get(plan)
    if session is not None:
        try:
            mark = session.find_case(case_id).mark
            if mark.login_username and mark.login_password:
                creds = (mark.login_username, mark.login_password)
        except KeyError:
            pass
    try:
        orch = _build_orchestrator(_make_on_update(run_id))
        final = await orch.run_single_case(
            case_id, plan_key=plan, step_indices=step_indices, credentials=creds,
            target_url=target_url,
        )
        RUNS[run_id] = final
        case = next((c for c in final.test_cases if c.id == case_id), None)
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        note = (
            compose_agent_note(case, run_id, step_indices, when)
            if case is not None
            else f"Agent run {when} ({run_id}): case missing from run state"
        )
        MANUAL.set_agent(
            plan, case_id, case.status if case else "blocked", run_id, agent_note=note
        )
    except asyncio.CancelledError:
        log.info("Manual agent run %s cancelled by tester", run_id)
        state.finish()
        _make_on_update(run_id)(state)
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        MANUAL.set_agent(
            plan, case_id, None, run_id,
            agent_note=f"Agent run {when} ({run_id}): cancelled by tester",
        )
        raise
    except Exception:
        log.exception("Manual agent run %s crashed", run_id)
        state.finish()
        _make_on_update(run_id)(state)
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        MANUAL.set_agent(
            plan, case_id, "blocked", run_id,
            agent_note=f"Agent run {when} ({run_id}): crashed — see server log",
        )


# ---------------------------------------------------------------- endpoints


def _parse_app_environments() -> list[dict]:
    """Parse `APP_ENVIRONMENTS` ("Name=url,Name=url") into [{"name", "url"}].

    A malformed entry (no "=", or an empty name/url) is skipped with a
    logged warning rather than crashing /config; unset or all-malformed
    yields [].
    """
    raw = os.environ.get("APP_ENVIRONMENTS", "")
    out: list[dict] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            log.warning("Skipping malformed APP_ENVIRONMENTS entry: %r", entry)
            continue
        name, _, url = entry.partition("=")
        name, url = name.strip(), url.strip()
        if not name or not url:
            log.warning("Skipping malformed APP_ENVIRONMENTS entry: %r", entry)
            continue
        out.append({"name": name, "url": url})
    return out


@app.get("/config")
async def get_config() -> dict:
    """Non-secret frontend bootstrap: which cycle to open by default, the
    known-server list for the global target URL picker, and its current
    value."""
    return {
        "default_cycle": os.environ.get("QMETRY_DEFAULT_CYCLE") or None,
        "environments": _parse_app_environments(),
        "default_url": os.environ.get("APP_BASE_URL") or None,
        "target_url": SETTINGS.get("target_url") or "",
    }


@app.post("/settings/target-url")
async def set_target_url(body: TargetUrlBody) -> dict:
    """Set the GLOBAL target URL — applies to every run, full-plan or single
    Manual case, until changed again. "" clears back to the .env
    APP_BASE_URL default. Persisted to settings.json so it survives a server
    restart. Not a secret."""
    url = _validate_target_url(body.url)
    SETTINGS.set("target_url", url)
    return {"target_url": url}


@app.get("/cycles")
async def list_cycles(q: str = "", start: int = 0, limit: int = 50) -> dict:
    """One page of newest-first QMetry test runs; empty in fixture mode.

    `q` is pushed down to QMetry's own substring filter on the run name, so the
    console searches all 400-odd runs rather than the page it happens to hold.
    Entries are {id, key, name}.
    """
    if not _qmetry_configured():
        return {
            "cycles": [], "total": 0, "start": start, "limit": limit,
            "next_start": start, "truncated": False,
        }
    try:
        page = await _make_qmetry_client().search_test_cycles(
            query=q or None, start_at=start, max_results=limit
        )
    except Exception as e:
        log.exception("Could not list QMetry cycles")
        raise HTTPException(502, f"Could not list cycles: {e}")
    return {
        "cycles": page["rows"],
        "total": page["total"],
        "start": start,
        "limit": limit,
        # Advance by what QMetry returned, not by how many rows survived
        # filtering — otherwise the next page skips or repeats rows.
        "next_start": start + page.get("page_size", len(page["rows"])),
        # True when a multi-term search stopped scanning at its cap, so `total`
        # is a floor rather than the exact count.
        "truncated": page.get("truncated", False),
    }


@app.get("/testcases")
async def list_project_testcases(q: str = "", start: int = 0, limit: int = 50) -> dict:
    """One page of the project's whole test case library; empty in fixture mode.

    The library is thousands of cases, so this is always paged and `q` is
    QMetry-side. Entries are {id, key, name}; `plan_key` is the synthetic
    one-case plan key to open the case with (see agent/qmetry.py).
    """
    if not _qmetry_configured():
        return {
            "cases": [], "total": 0, "start": start, "limit": limit,
            "next_start": start, "truncated": False,
        }
    from agent.qmetry import standalone_plan_key

    try:
        page = await _make_qmetry_client().search_project_test_cases(
            query=q or None, start_at=start, max_results=limit
        )
    except Exception as e:
        log.exception("Could not list QMetry test cases")
        raise HTTPException(502, f"Could not list test cases: {e}")
    return {
        "cases": [
            {
                "id": r["id"],
                "key": r["key"],
                "name": r["name"],
                "plan_key": standalone_plan_key(r["key"]),
            }
            for r in page["rows"]
        ],
        "total": page["total"],
        "start": start,
        "limit": limit,
        "next_start": start + page.get("page_size", len(page["rows"])),
        "truncated": page.get("truncated", False),
    }


@app.post("/runs")
async def start_run(body: StartRunBody) -> dict:
    """Kick off a plan run; return its run id so the frontend can subscribe."""
    # Eagerly construct the RunState so the GET endpoint works immediately.
    # The orchestrator will overwrite RUNS[run_id] with its own state when it
    # starts producing updates.
    from agent.run_state import new_run_state

    state = new_run_state(body.plan)
    RUNS[state.run_id] = state
    LATEST[state.run_id] = state.to_dict()
    LISTENERS.setdefault(state.run_id, [])

    if body.username and body.password:
        RUN_CREDENTIALS[state.run_id] = (body.username, body.password)

    task = asyncio.create_task(_run_in_background(state.run_id, body.plan, state))
    TASKS[state.run_id] = task
    return {"run_id": state.run_id}


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    # LATEST holds the live snapshot, updated on every transition via on_update;
    # RUNS[run_id] is only replaced with the final state when the whole run
    # finishes. Prefer LATEST so polling reflects live progress instead of the
    # initial idle state. Fall back to RUNS (and 404) when no snapshot exists.
    snapshot = LATEST.get(run_id)
    if snapshot is not None:
        return snapshot
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "Unknown run id")
    return state.to_dict()


@app.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    """Cancel a running background task (full plan run or single-case agent run)."""
    task = TASKS.get(run_id)
    if task is None or task.done():
        raise HTTPException(404, "no cancellable run")
    task.cancel()
    return {"cancelled": True}


@app.post("/runs/{run_id}/push-qmetry")
async def push_run_to_qmetry(run_id: str, body: PushBody | None = None) -> dict:
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
    # Only the QMetry ids are needed here — the step text comes from the run.
    src_cases = {
        c["id"]: c for c in await source.list_cases(state.plan.key, with_steps=False)
    }

    from agent.qmetry import QMetryClient, QMetryError, write_case_execution

    client = QMetryClient()
    mode = body.mode if (body and body.mode) else _qmetry_execution_mode()
    pushed: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []
    try:
        for case in state.test_cases:
            src = src_cases.get(case.id)
            if src is None or src.get("_qmetry_execution_id") is None:
                skipped.append(case.id)
                continue
            # Positional map: run-tape order == QMetry step-execution order for a
            # full plan run (this endpoint's use). A partial/filtered run_state
            # would misalign — the Live UI only pushes full plan runs.
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


@app.get("/manual/{plan}")
async def get_manual(plan: str) -> dict:
    """Build (or rebuild) the manual session for a plan and return its state."""
    source = _make_case_source()
    try:
        meta = await source.get_plan(plan)
        # Steps-less: one QMetry call for the whole run instead of one per case.
        # The console fetches the steps of the case the tester opens.
        cases = await source.list_cases(plan, with_steps=False)
    except Exception as e:
        log.exception("Could not load manual plan %s", plan)
        raise HTTPException(502, f"Could not load plan from source: {e}")
    from agent.qmetry import is_standalone_plan

    session = MANUAL.build(
        plan,
        meta.get("name", plan),
        cases,
        _qmetry_configured(),
        standalone=is_standalone_plan(plan),
        display_key=meta.get("key") or plan,
    )

    # Un-stick cases left mid-run. `agent_status: "running"` is persisted, but
    # the run state that would finish it lives in memory — so a crash, a kill or
    # a restart strands the case as "running" forever, which disables both Run
    # and Push for it. If the run id isn't one this process owns, the run is
    # gone and cannot come back.
    for case in session.cases:
        if case.mark.agent_status == "running" and case.mark.agent_run_id not in RUNS:
            note = case.mark.agent_note or ""
            suffix = f"Agent run {case.mark.agent_run_id}: interrupted (server restarted)"
            MANUAL.set_agent(
                plan, case.id, None, case.mark.agent_run_id,
                agent_note=f"{note}\n{suffix}".strip() if note else suffix,
            )
            log.info("Cleared stranded 'running' agent status on %s/%s", plan, case.id)

    return session.to_dict()


@app.get("/manual/{plan}/cases/{case_id}/steps")
async def get_manual_case_steps(plan: str, case_id: str) -> dict:
    """Fetch one case's steps on demand and return the updated case.

    Idempotent and cached in the case source, so re-opening a case costs
    nothing. Requires the session to exist (GET /manual/{plan} first).
    """
    session = MANUAL.get(plan)
    if session is None:
        raise HTTPException(404, f"No manual session for plan {plan!r}; GET it first")
    try:
        case = session.find_case(case_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if case.steps_loaded:
        return case.to_dict()

    source = _make_case_source()
    try:
        steps = await source.get_case_steps(plan, case_id)
        # Free after the steps call — it came back in the same response.
        test_data = await source.get_case_test_data(plan, case_id)
    except Exception as e:
        log.exception("Could not load steps for %s/%s", plan, case_id)
        raise HTTPException(502, f"Could not load steps: {e}")
    return MANUAL.set_steps(plan, case_id, steps, test_data).to_dict()


@app.post("/manual/{plan}/cases/{case_id}/mark")
async def mark_case(plan: str, case_id: str, body: MarkBody) -> dict:
    """Record a hand mark on a case; returns the updated case dict."""
    try:
        case = MANUAL.set_mark(plan, case_id, body.status, body.comment, body.failed_steps)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return case.to_dict()


@app.post("/manual/{plan}/cases/{case_id}/steps/{step_index}/mark")
async def mark_step(plan: str, case_id: str, step_index: int, body: StepMarkBody) -> dict:
    """Record a hand mark on a single step (pass/fail/blocked/skip).

    A mark that contradicts a non-null `agent_status` is an override — it
    requires a note and (best-effort) gets appended to the knowledge file so
    future evaluator runs of this exact step see the tester's ruling.
    """
    session = MANUAL.get(plan)
    if session is None:
        raise HTTPException(404, f"No manual session for plan {plan!r}; GET it first")
    try:
        case = session.find_case(case_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if step_index < 0 or step_index >= len(case.steps):
        raise HTTPException(
            404, f"step index {step_index} out of range for case {case_id!r}"
        )

    overrode = bool(body.agent_status) and body.status != body.agent_status
    if overrode and not body.note.strip():
        raise HTTPException(422, "override requires a note")

    try:
        case = MANUAL.set_step_mark(
            plan, case_id, step_index, body.status, body.note, body.agent_status
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    if overrode:
        try:
            step = case.steps[step_index]
            record_override(
                plan,
                case_id,
                step_index,
                step.get("action", ""),
                step.get("expected", ""),
                body.agent_status,
                body.status,
                body.note,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
        except Exception:
            log.exception(
                "Could not record knowledge override for %s/%s step %s",
                plan, case_id, step_index,
            )

    return case.to_dict()


@app.post("/manual/{plan}/cases/{case_id}/credentials")
async def set_case_credentials(plan: str, case_id: str, body: CredentialsBody) -> dict:
    """Per-case login for the agent. Kept separate from /mark so status and
    note updates never carry credentials. The response and all /manual
    payloads contain the username only — never the password."""
    try:
        case = MANUAL.set_credentials(plan, case_id, body.username, body.password)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return case.to_dict()


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


@app.post("/manual/{plan}/push-qmetry")
async def push_manual_to_qmetry(plan: str, body: PushBody | None = None) -> dict:
    """Gated: push marked manual results to the QMetry cycle.

    Skips cases that are unmarked or have no QMetry execution id. Per-case
    failures are reported, not fatal.
    """
    if not _qmetry_configured():
        raise HTTPException(409, "QMetry is not configured — set QMETRY_API_KEY first")

    from agent.qmetry import is_standalone_plan

    if is_standalone_plan(plan):
        raise HTTPException(
            409,
            "A standalone test case has no execution to write to — open it "
            "through a test run to record results in QMetry",
        )

    session = MANUAL.get(plan)
    if session is None:
        raise HTTPException(404, f"No manual session for plan {plan!r}; GET it first")

    marked = [c for c in session.cases if c.mark.status != "unmarked"]
    if not marked:
        raise HTTPException(409, "Nothing marked — mark at least one case first")

    from agent.qmetry import QMetryClient, QMetryError, write_case_execution

    client = QMetryClient()
    mode = body.mode if (body and body.mode) else _qmetry_execution_mode()
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


@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str):
    """Server-Sent Events — emits a run_state snapshot on every transition."""
    if run_id not in RUNS:
        raise HTTPException(404, "Unknown run id")

    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    LISTENERS.setdefault(run_id, []).append(q)

    async def _events():
        try:
            # First frame: current snapshot so a late subscriber doesn't see an empty UI.
            yield {"event": "state", "data": json.dumps(LATEST.get(run_id, {}))}
            while True:
                snapshot = await q.get()
                yield {"event": "state", "data": json.dumps(snapshot)}
                if snapshot.get("status") == "done":
                    break
        finally:
            try:
                LISTENERS.get(run_id, []).remove(q)
            except ValueError:
                pass

    return EventSourceResponse(_events())


@app.post("/runs/{run_id}/report")
async def post_report(run_id: str) -> dict:
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "Unknown run id")
    if state.status != "done":
        raise HTTPException(409, "Run is not finished yet")
    # Defer to reporter.py once implemented.
    from agent.reporter import generate_report

    try:
        path = generate_report(state)
    except NotImplementedError:
        raise HTTPException(501, "Reporter not implemented yet")
    # Return an HTTP URL the browser can open in a new tab, not a filesystem
    # path — `file://` navigation from an `http://` page is blocked by Chrome.
    # Served by the "/reports" static mount below.
    return {"path": f"/reports/{path.name}", "filename": path.name}


@app.post("/runs/{run_id}/log-bugs")
async def post_log_bugs(run_id: str) -> dict:
    """Gated: only on a finished run with at least one failure."""
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "Unknown run id")
    if state.status != "done":
        raise HTTPException(409, "Run must be finished before logging bugs")
    if state.summary["failed"] == 0:
        raise HTTPException(409, "No failures to log")

    from agent.jira_client import JiraClient, JiraError, bugs_from_failed_run

    try:
        jira = JiraClient()
    except KeyError as e:
        raise HTTPException(500, f"Jira not configured: missing {e.args[0]}")

    created: list[dict] = []
    errors: list[str] = []
    try:
        for bug in bugs_from_failed_run(state):
            try:
                resp = await jira.create_bug(bug["summary"], bug["description"], labels=["qa-agent"])
                created.append({"key": resp.get("key"), "summary": bug["summary"]})
            except JiraError as e:
                errors.append(str(e))
    finally:
        await jira.aclose()

    return {"created": created, "errors": errors}


# Generated HTML reports (agent/reporter.py). Mounted before the frontend
# catch-all so "/reports/*" resolves here instead of being swallowed by it.
# No auth: the server binds 127.0.0.1 only.
_REPORTS_DIR = Path(__file__).resolve().parent / "reports"
_REPORTS_DIR.mkdir(exist_ok=True)
app.mount("/reports", StaticFiles(directory=_REPORTS_DIR), name="reports")

# Static frontend (production). Mounted last so /runs/* and /reports/* routes win.
_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    # NOTE: reload is OFF on purpose. With --reload, uvicorn runs the app in a
    # worker process whose event loop is built before the module-level
    # ProactorEventLoop policy (above) applies — so Playwright's browser
    # subprocess fails with NotImplementedError and every agent run blocks.
    # Single-process + app object keeps us on the Proactor loop. Restart the
    # server by hand after code changes.
    uvicorn.run(
        app,
        host=os.environ.get("SERVER_HOST", "127.0.0.1"),
        port=int(os.environ.get("SERVER_PORT", "8000")),
    )
