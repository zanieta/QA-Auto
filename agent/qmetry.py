"""QMetry REST API client.

API host:  https://qtmcloud.qmetry.com
Base path: /rest/api/latest
Auth:      apiKey header (from QMetry Configuration → Open API)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from agent.case_source import CaseSource  # noqa: F401 – re-exported for convenience

log = logging.getLogger(__name__)

_BASE = "https://qtmcloud.qmetry.com/rest/api/latest"
_MAX_RESULTS = 100
_MAX_RETRIES = 3

# Loaded cases per cycle, shared across QMetryCaseSource instances (the server
# builds a fresh source per request). Marks refresh the manual view constantly;
# without this every refresh re-crawls QMetry (2 calls per test case).
_CASES_CACHE: dict[str, tuple[float, list]] = {}
_CASES_CACHE_TTL_S = 60.0
# How many test cases to hydrate concurrently (name + steps per case).
_CASE_FETCH_CONCURRENCY = 8


def clean_step_text(text: str | None) -> str:
    """Strip Jira wiki markup from QMetry step text so the translator and the
    tester both get plain English.

    QMetry stores stepDetails/expectedResult as Jira wiki markup: `h4.`
    headings, `#` lists, `{{monospace}}`, `{panel}` blocks, `!image-url!`
    embeds, `*bold*`, and — worst for the agent — unresolved `[~id]` mentions
    that would otherwise be typed verbatim into form fields (seen live:
    "[~20322]" submitted as a login email). Mentions become the explicit
    marker "[unresolved reference]" so no model mistakes them for values.
    """
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"\{panel[^}]*\}", "", s, flags=re.IGNORECASE)  # {panel:…} … {panel}
    s = re.sub(r"\{color[^}]*\}", "", s, flags=re.IGNORECASE)
    s = re.sub(r"!https?://[^!]*!", "[image]", s)  # !image-url|width=…!
    s = re.sub(r"\{\{([^}]*)\}\}", r"\1", s)  # {{monospace}} → monospace
    s = re.sub(r"\[~[^\]]+\]", "[unresolved reference]", s)  # [~accountid]
    s = re.sub(r"^h[1-6]\.\s*", "", s, flags=re.MULTILINE)  # h4. heading prefix

    # List items keep their nesting depth — flattening makes an evaluator read
    # sub-items (e.g. menu entries under Recipe) as top-level requirements.
    def _bullet(m: re.Match) -> str:
        depth = len(m.group(1))
        return "  " * (depth - 1) + ("• " if depth == 1 else "- ")

    s = re.sub(r"^#(\*+)\s*", lambda m: "  " * len(m.group(1)) + "- ", s, flags=re.MULTILINE)
    s = re.sub(r"^#\s*", "• ", s, flags=re.MULTILINE)  # numbered list item
    s = re.sub(r"^(\*+)\s+", _bullet, s, flags=re.MULTILINE)  # */**/*** bullets
    s = re.sub(r"\*([^*\n]+)\*", r"\1", s)  # *bold* → bold
    s = re.sub(r"\n{3,}", "\n\n", s)  # collapse blank runs
    return s.strip()


class QMetryError(Exception):
    pass


class QMetryClient:
    """Async client for the QMetry for Jira Cloud REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        project_id: str | None = None,
        timeout: float = 30.0,
    ):
        self._api_key = api_key or os.environ["QMETRY_API_KEY"]
        self._project_id = project_id or os.environ.get("QMETRY_PROJECT_ID", "")
        self._timeout = timeout
        self._exec_result_cache: dict[str, int] = {}  # status_lower -> id

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "apiKey": self._api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ----------------------------------------------------------------- public

    async def get_test_cycle(self, cycle_key: str) -> dict[str, Any]:
        """GET /testcycles/{idOrKey} — returns cycle metadata.

        The real API wraps the cycle under a ``data`` key:
        ``{"data": {"id": ..., "key": ..., ...}}``. Unwrap it so callers get
        the cycle object directly.
        """
        resp = await self._request("GET", f"/testcycles/{cycle_key}")
        if isinstance(resp, dict) and "data" in resp:
            return resp["data"]
        return resp

    async def list_test_cycles(self, max_results: int = 50) -> list[dict[str, str]]:
        """POST /testcycles/search — newest-first cycles for the project.

        The API exposes NO name/summary for cycles (verified live 2026-07-03);
        each entry is {"id", "key"} only. Archived cycles are dropped.
        """
        data = await self._request(
            "POST",
            "/testcycles/search",
            params={"startAt": 0, "maxResults": max_results},
            json={"filter": {"projectId": self._project_id}},
        )
        rows = data.get("data") or []
        return [
            {"id": r["id"], "key": r.get("key", r["id"])}
            for r in rows
            if not r.get("archived")
        ]

    async def search_test_cases(self, cycle_id: str) -> list[dict[str, Any]]:
        """POST /testcycles/{id}/testcases/search — paginated.

        The real API requires a (non-null) ``filter`` object in the body; an
        empty ``{}`` returns all linked cases. Each entry carries at least:
          id, key, testCaseExecutionId, versionNo, tcvId
        (note: NOT ``summary`` — the case name lives on the version detail.)
        """
        results: list[dict] = []
        start_at = 0
        while True:
            data = await self._request(
                "POST",
                f"/testcycles/{cycle_id}/testcases/search",
                params={"startAt": start_at, "maxResults": _MAX_RESULTS},
                json={"filter": {}},
            )
            page: list[dict] = data.get("data") or []
            if not page:
                break
            results.extend(page)
            total: int = data.get("total", len(results))
            start_at += len(page)
            if start_at >= total:
                break
        return results

    async def get_test_case_versions(self, tc_id: str) -> list[dict[str, Any]]:
        """GET /testcases/{id} — returns list of {versionNo, isLatestVersion}."""
        resp = await self._request("GET", f"/testcases/{tc_id}")
        return resp if isinstance(resp, list) else resp.get("data", [resp])

    async def get_test_case_version_detail(
        self, tc_id: str, version_no: int
    ) -> dict[str, Any]:
        """GET /testcases/{id}/versions/{no} — detail incl. ``summary``.

        Real response wraps the detail under ``data``; the case name is
        ``data.summary``. The API omits ``precondition`` unless the query
        names it explicitly (``fields=all`` does NOT work — verified live).
        """
        resp = await self._request(
            "GET",
            f"/testcases/{tc_id}/versions/{version_no}",
            params={"fields": "summary,precondition"},
        )
        if isinstance(resp, dict) and "data" in resp:
            return resp["data"]
        return resp

    async def get_test_steps(
        self, tc_id: str, version_no: int
    ) -> list[dict[str, Any]]:
        """POST /testcases/{id}/versions/{no}/teststeps/search — paginated.

        Each step has: id, seqNo, stepDetails, expectedResult, testData.
        """
        results: list[dict] = []
        start_at = 0
        while True:
            data = await self._request(
                "POST",
                f"/testcases/{tc_id}/versions/{version_no}/teststeps/search",
                params={"startAt": start_at, "maxResults": _MAX_RESULTS},
                json={},
            )
            page: list[dict] = data.get("data") or []
            if not page:
                break
            results.extend(page)
            total: int = data.get("total", len(results))
            start_at += len(page)
            if start_at >= total:
                break
        return results

    async def get_execution_results(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /projects/{projectId}/execution-results — PASS/FAIL/BLOCKED ids."""
        pid = project_id or self._project_id
        data = await self._request("GET", f"/projects/{pid}/execution-results")
        return data if isinstance(data, list) else data.get("data", [])

    async def post_execution_result(
        self,
        cycle_id: str,
        execution_id: int,
        status: str,
        comment: str | None = None,
    ) -> None:
        """PUT /testcycles/{id}/testcase-executions/{executionId}.

        status: "pass" | "fail" | "blocked" (case-insensitive match to QMetry names).
        """
        if not self._exec_result_cache:
            results = await self.get_execution_results()
            self._exec_result_cache = {r["name"].lower(): r["id"] for r in results}

        result_id = self._exec_result_cache.get(status.lower())
        if result_id is None:
            log.warning("Unknown QMetry execution status %r; skipping post", status)
            return

        body: dict[str, Any] = {"executionResultId": result_id}
        if comment:
            body["comment"] = comment[:4000]  # QMetry comment limit

        await self._request(
            "PUT",
            f"/testcycles/{cycle_id}/testcase-executions/{execution_id}",
            json=body,
        )

    async def get_test_step_executions(
        self, cycle_id: str, exec_id: int
    ) -> list[dict[str, Any]]:
        """GET the step-execution rows for one test-case execution, in order.

        Endpoint verified live 2026-07-21:
        ``/testcycles/{cycle}/testcase-executions/{exec}/teststeps`` (NOT
        ``teststep-executions``). Each row's step-execution id is
        ``testStepExecutionId``; it is copied to ``id`` so callers stay
        decoupled from the QMetry field name. Row order matches the flattened
        step order the CaseSource produced (verified against a shareable case).
        """
        data = await self._request(
            "GET",
            f"/testcycles/{cycle_id}/testcase-executions/{exec_id}/teststeps",
        )
        rows = data if isinstance(data, list) else data.get("data", [])
        for row in rows:
            if isinstance(row, dict) and "id" not in row and "testStepExecutionId" in row:
                row["id"] = row["testStepExecutionId"]
        return rows

    async def post_step_execution_result(
        self,
        cycle_id: str,
        exec_id: int,
        step_exec_id: int,
        status: str,
        comment: str | None = None,
    ) -> None:
        """PUT one step's result. Unknown status name -> log + skip (no PUT).

        Endpoint verified live 2026-07-21:
        ``/testcycles/{cycle}/testcase-executions/{exec}/teststeps/{step_exec_id}``
        (NOT ``teststep-executions``). The write body (``executionResultId`` +
        optional ``comment``) is INFERRED from the working case-level
        ``post_execution_result`` convention — writes were not performed during
        probing, so this must be confirmed on the first real push.
        """
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
            f"/teststeps/{step_exec_id}",
            json=body,
        )

    async def create_execution(
        self, cycle_id: str, tc_id: str, version_no: int
    ) -> int:
        """Create a fresh execution run of a test case inside an EXISTING cycle.

        Returns the new test-case-execution id. Used only by
        QMETRY_EXECUTION_MODE=create — the app never creates whole test cycles.

        NOT live-verified: create writes were intentionally not performed
        during the 2026-07-21 probe (read-only). This endpoint/body must be
        confirmed on first real use in create mode.
        """
        data = await self._request(
            "POST",
            f"/testcycles/{cycle_id}/testcase-executions",
            json={"tcId": tc_id, "tcVersionNo": version_no},
        )
        payload = data.get("data", data) if isinstance(data, dict) else data
        return payload["id"]

    # -------------------------------------------------------------- internals

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> Any:
        url = _BASE + path
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=self._headers,
                        params=params,
                        json=json,
                    )
                if response.status_code == 429:
                    log.warning(
                        "QMetry rate-limited; retrying in %.1fs (attempt %d/%d)",
                        delay,
                        attempt,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                response.raise_for_status()
                return response.json() if response.content else {}
            except httpx.HTTPStatusError as exc:
                log.warning(
                    "QMetry %s %s → %s (attempt %d/%d)",
                    method,
                    path,
                    exc.response.status_code,
                    attempt,
                    _MAX_RETRIES,
                )
                last_exc = QMetryError(
                    f"QMetry {method} {path} returned {exc.response.status_code}: "
                    f"{exc.response.text[:200]}"
                )
                if exc.response.status_code in (400, 401, 403, 404):
                    raise last_exc from exc  # don't retry client errors
                await asyncio.sleep(delay)
                delay *= 2
            except httpx.RequestError as exc:
                log.warning(
                    "QMetry network error %s %s: %s (attempt %d/%d)",
                    method,
                    path,
                    exc,
                    attempt,
                    _MAX_RETRIES,
                )
                last_exc = QMetryError(f"QMetry network error on {path}: {exc}")
                await asyncio.sleep(delay)
                delay *= 2
        raise last_exc or QMetryError(
            f"QMetry {method} {path} failed after {_MAX_RETRIES} attempts"
        )


@dataclass
class WriteResult:
    """Outcome of a `write_case_execution` call."""

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

    ``mode="edit"`` writes into ``execution_id``. ``mode="create"`` creates a
    fresh execution run in the SAME cycle and writes into it. ``step_results``
    maps a flattened step index to ``(status, comment)``; it is mapped onto
    the execution's step-execution rows BY POSITION (index into the rows
    list). An index out of range for the current rows is recorded as an
    error and skipped. A per-step post that raises `QMetryError` is recorded
    as an error and never re-raised. The case-level result is always
    attempted afterward.
    """
    exec_id = execution_id
    if mode == "create":
        exec_id = await client.create_execution(cycle_id, tc_id, version_no)

    # Case-level result FIRST — QMetry cascades a case result onto every step
    # row, so the per-step writes must come AFTER it or they get clobbered
    # (verified live 2026-07-22: steps-then-case left every step = case status).
    await client.post_execution_result(
        cycle_id=cycle_id, execution_id=exec_id, status=case_status, comment=comment
    )

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
            log.error(
                "QMetry step result write failed for step_exec_id=%s: %s",
                step_exec_id,
                e,
            )
            errors.append(
                {"step_index": idx, "step_exec_id": step_exec_id, "error": str(e)}
            )

    return WriteResult(exec_id=exec_id, steps_written=steps_written, errors=errors)


class QMetryCaseSource:
    """CaseSource backed by the QMetry REST API.

    Pass a cycle key or id (e.g. ``1ZwYH2ObF7AGZa``) as ``plan_key``.
    Each returned case carries private ``_qmetry_*`` fields that
    can be used later to post execution results.
    """

    def __init__(
        self,
        client: QMetryClient | None = None,
    ):
        self._client = client or QMetryClient()
        self._cycle_cache: dict[str, dict] = {}

    async def get_plan(self, plan_key: str) -> dict[str, str]:
        cycle = await self._fetch_cycle(plan_key)
        # The cycle endpoint returns no name/summary, so fall back to the human
        # cycle key (e.g. SOUSCLOUD-TR-482) for a readable label.
        return {
            "key": cycle.get("key", plan_key),
            "name": cycle.get("summary") or cycle.get("key") or plan_key,
        }

    async def list_cases(self, plan_key: str) -> list[dict[str, Any]]:
        import time

        cached = _CASES_CACHE.get(plan_key)
        if cached and (time.monotonic() - cached[0]) < _CASES_CACHE_TTL_S:
            return cached[1]

        cycle = await self._fetch_cycle(plan_key)
        cycle_id: str = cycle["id"]

        tc_entries = await self._client.search_test_cases(cycle_id)

        # Hydrating a case takes 2 QMetry calls (name + steps); do them with
        # bounded concurrency — sequentially a big cycle takes minutes.
        sem = asyncio.Semaphore(_CASE_FETCH_CONCURRENCY)

        async def _hydrate(entry: dict) -> dict:
            tc_id = entry.get("id")
            tc_key = entry.get("key") or str(tc_id)
            version_no = entry.get("versionNo", 1)
            # Real API: execution id is ``testCaseExecutionId`` (not latestTc…).
            exec_id = entry.get("testCaseExecutionId")
            async with sem:
                # The case name (``summary``) is only on the version detail.
                name = tc_key
                precondition = ""
                try:
                    detail = await self._client.get_test_case_version_detail(
                        tc_id, version_no
                    )
                    name = detail.get("summary") or tc_key
                    precondition = clean_step_text(detail.get("precondition") or "")
                except QMetryError:
                    log.warning("Could not load name for %s", tc_key, exc_info=True)

                steps = await self._load_steps(tc_id, version_no, tc_key)

            return {
                "id": tc_key,
                "name": name,
                "precondition": precondition,
                "steps": steps,
                # Private fields used for writing results back
                "_qmetry_cycle_id": cycle_id,
                "_qmetry_execution_id": exec_id,
                "_qmetry_tc_id": tc_id,
                "_qmetry_version_no": version_no,
            }

        cases = list(await asyncio.gather(*(_hydrate(e) for e in tc_entries)))
        _CASES_CACHE[plan_key] = (time.monotonic(), cases)

        return cases

    # ------------------------------------------------------------ internals

    async def _fetch_cycle(self, cycle_key: str) -> dict:
        if cycle_key not in self._cycle_cache:
            self._cycle_cache[cycle_key] = await self._client.get_test_cycle(
                cycle_key
            )
        return self._cycle_cache[cycle_key]

    async def _load_steps(
        self, tc_id: str, version_no: int, tc_key: str
    ) -> list[dict[str, str]]:
        """Load + flatten a case's steps for the given version.

        The API returns steps in order. A step is either a plain step (with
        ``stepDetails`` / ``expectedResult``) or a *shareable* reference whose
        real steps live under ``shareable.shareableTestSteps`` — expand those
        inline so the tester sees a single flat list.
        """
        try:
            raw_steps = await self._client.get_test_steps(tc_id, version_no)
        except QMetryError:
            log.warning("Could not load steps for %s", tc_key, exc_info=True)
            return []

        def _action(step: dict) -> str:
            """Step action text = cleaned details, plus the testData values —
            the inputs the tester is meant to enter — when present."""
            action = clean_step_text(step.get("stepDetails"))
            test_data = clean_step_text(step.get("testData"))
            if test_data:
                action = f"{action}\nTest data: {test_data}"
            return action

        flat: list[dict[str, str]] = []
        for s in raw_steps:
            if s.get("stepDetails"):
                flat.append(
                    {
                        "action": _action(s),
                        "expected": clean_step_text(s.get("expectedResult")),
                    }
                )
                continue
            shareable = s.get("shareable") or {}
            for sub in shareable.get("shareableTestSteps", []):
                flat.append(
                    {
                        "action": _action(sub),
                        "expected": clean_step_text(sub.get("expectedResult")),
                    }
                )
        return flat
