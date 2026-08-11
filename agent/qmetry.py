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
# without this every refresh re-crawls QMetry.
_CASES_CACHE: dict[str, tuple[float, list]] = {}
_CASES_CACHE_TTL_S = 60.0
# Steps per (plan_key, case id), kept for the process lifetime. Steps are the
# expensive part (one call per case) and they don't change during a session, so
# they outlive the short-lived case-list cache: a list refresh re-attaches them
# instead of making the tester's open case go blank and re-fetch.
_STEPS_CACHE: dict[tuple[str, str], list[dict]] = {}
# A case's parameter table ("Test Data" in QMetry's UI), cached alongside its
# steps — it arrives from the same call and has the same lifetime.
_CASE_TEST_DATA_CACHE: dict[tuple[str, str], list[dict]] = {}
# How many test cases to hydrate steps for concurrently.
_CASE_FETCH_CONCURRENCY = 8

# Cases and cycles only carry `summary` / `precondition` when the query names
# the fields explicitly — `fields=all` silently omits them (verified live
# 2026-08-04). Asking for them here is what lets a cycle's case list load in a
# single call instead of one version-detail call per case.
_CASE_FIELDS = "key,summary,precondition"
_CYCLE_FIELDS = "key,summary,description"

# Multi-term catalogue search has to AND terms locally (QMetry's search is a
# single substring with no AND and no wildcards), so it scans the pages of the
# most selective term. The cap keeps one keystroke from becoming dozens of
# calls when every term is common; the cache makes typing further terms free.
_MAX_SCAN_PAGES = 12  # x _MAX_RESULTS rows
_SCAN_CACHE: dict[tuple[str, str], tuple[float, list, bool]] = {}
_TERM_TOTAL_CACHE: dict[tuple[str, str], tuple[float, int]] = {}
_SCAN_CACHE_TTL_S = 60.0

# "2075" | "TC-2075" | "tc 2075" | "SOUSCLOUD-TC-2075" — testers search by key,
# and a key never appears in the name, so these need an exact key lookup.
# The negative lookahead matters: without it the project-prefix group greedily
# eats the "TC-" in "TC-2075" and the key comes out as "TC-TC-2075".
_KEY_QUERY_RE = re.compile(
    r"^\s*(?:(?!T[CR][-\s])([A-Za-z][A-Za-z0-9_]*)-)?(?:(TC|TR)[-\s]?)?(\d+)\s*$",
    re.IGNORECASE,
)

# Plan-key prefix for a standalone test case opened straight from the project
# library (TC mode in the console) rather than through a test cycle. Such a
# "plan" holds exactly one case and has no execution to write results into.
STANDALONE_PREFIX = "TC:"


def is_standalone_plan(plan_key: str) -> bool:
    """True for the synthetic one-case plan key used by TC mode."""
    return plan_key.startswith(STANDALONE_PREFIX)


def standalone_plan_key(case_key: str) -> str:
    return f"{STANDALONE_PREFIX}{case_key}"


def clean_step_text(
    text: str | None, params: dict[str, str] | None = None
) -> str:
    """Strip Jira wiki markup from QMetry step text so the translator and the
    tester both get plain English.

    QMetry stores stepDetails/expectedResult as Jira wiki markup: `h4.`
    headings, `#` lists, `{{monospace}}`, `{panel}` blocks, `!image-url!`
    embeds, `*bold*`, and `[~id]` tokens.

    Those `[~id]` tokens are **test-case parameters**, not user mentions:
    `[~22720]` is the "Menu" parameter, whose value for a given case might be
    "Recipe". Pass `params` (id -> value, from
    ``QMetryClient.get_test_case_parameters``) and they are replaced with the
    real value, which is the difference between the agent reading
    'locate the "Recipe" menu' and 'locate the "[unresolved reference]" menu' —
    the latter sent it wandering into the wrong page and failed 14 of 26 steps
    in TC-1985 (2026-07-14).

    Anything still unresolved becomes the explicit marker
    "[unresolved reference]" so no model mistakes the raw token for a value
    (seen live: "[~20322]" typed into a login email field).
    """
    if not text:
        return ""
    s = str(text)
    if params:
        # Longest ids first so no id is a prefix of another.
        for pid in sorted(params, key=len, reverse=True):
            value = params[pid]
            if value:
                s = s.replace(f"[~{pid}]", value)
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


def params_map(parameters: list[dict[str, str]]) -> dict[str, str]:
    """Parameter rows -> the {id: value} map `clean_step_text` substitutes with."""
    return {p["id"]: p["value"] for p in parameters}


class QMetryError(Exception):
    pass


class QMetryClient:
    """Async client for the QMetry for Jira Cloud REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        project_id: str | None = None,
        timeout: float = 30.0,
        project_key: str | None = None,
    ):
        self._api_key = api_key or os.environ["QMETRY_API_KEY"]
        self._project_id = project_id or os.environ.get("QMETRY_PROJECT_ID", "")
        # Needed to expand a shorthand key query ("2075") into the full key
        # QMetry matches on. JIRA_PROJECT_KEY is the same project.
        self._project_key = (
            project_key
            or os.environ.get("QMETRY_PROJECT_KEY")
            or os.environ.get("JIRA_PROJECT_KEY", "")
        )
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
        resp = await self._request(
            "GET", f"/testcycles/{cycle_key}", params={"fields": _CYCLE_FIELDS}
        )
        if isinstance(resp, dict) and "data" in resp:
            return resp["data"]
        return resp

    async def search_test_cycles(
        self,
        query: str | None = None,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """POST /testcycles/search — one newest-first page of cycles.

        Returns ``{"total": n, "rows": [...], "page_size": m}`` where rows are
        ``{"id", "key", "name"}``. Cycles DO expose a name — it arrives as
        ``summary``, but only when the query names the field (verified live
        2026-08-04, correcting the earlier "cycles have no name" finding).
        ``query`` is QMetry's own case-insensitive substring filter on that
        name, so search covers every cycle in the project rather than the page
        in hand.

        Archived cycles are excluded by QMetry itself (``archived: False``), not
        after the fact: dropping rows locally would leave the caller's paging
        offset out of step with the server's and silently skip cycles.
        ``page_size`` is the raw row count so a caller can advance exactly.
        """
        page = await self._search_catalogue(
            "/testcycles/search",
            base_filter={"projectId": self._project_id, "archived": False},
            fields=_CYCLE_FIELDS,
            entity="TR",
            query=query,
            start_at=start_at,
            max_results=max_results,
            drop=lambda r: bool(r.get("archived")),
        )
        return {
            **page,
            "rows": [
                {
                    "id": r["id"],
                    "key": r.get("key", r["id"]),
                    "name": r.get("summary") or r.get("key") or r["id"],
                }
                for r in page["rows"]
            ],
        }

    async def list_test_cycles(self, max_results: int = 50) -> list[dict[str, str]]:
        """Newest-first cycles for the project — the first page only."""
        return (await self.search_test_cycles(max_results=max_results))["rows"]

    async def search_project_test_cases(
        self,
        query: str | None = None,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """POST /testcases/search — one page of the whole project's test cases.

        The project library is far too large to load in one go (2534 cases live
        as of 2026-08-04), so this is always paged and ``query`` is pushed down
        to QMetry's own substring filter.

        Archived cases and shareable step containers are dropped — neither is
        something a tester can execute. QMetry already omits both from this
        resource (live-checked), so that drop is a belt-and-braces guard rather
        than the real filter; ``page_size`` reports the raw row count so a caller
        advances by what the server returned, not by what survived the guard.
        Returns ``{"total": n, "rows": [{"id", "key", "name", "precondition",
        "version_no"}], "page_size": m}``.
        """
        page = await self._search_catalogue(
            "/testcases/search",
            base_filter={"projectId": self._project_id},
            fields=_CASE_FIELDS,
            entity="TC",
            query=query,
            start_at=start_at,
            max_results=max_results,
            drop=lambda r: bool(r.get("archived") or r.get("shareable")),
        )
        return {
            **page,
            "rows": [
                {
                    "id": r["id"],
                    "key": r.get("key", r["id"]),
                    "name": r.get("summary") or r.get("key") or r["id"],
                    "precondition": clean_step_text(r.get("precondition")),
                    "version_no": (r.get("version") or {}).get("versionNo", 1),
                }
                for r in page["rows"]
            ],
        }

    # ------------------------------------------------- catalogue search internals

    async def _search_catalogue(
        self,
        path: str,
        *,
        base_filter: dict[str, Any],
        fields: str,
        entity: str,
        query: str | None,
        start_at: int,
        max_results: int,
        drop,
    ) -> dict[str, Any]:
        """One page of a searchable catalogue (test runs or test cases).

        QMetry's search is a single case-insensitive **substring** on one field.
        It supports no AND, no wildcards, and silently *ignores* filter keys it
        doesn't know (an `and: [...]` filter returns the whole project, which is
        worse than an error). So the three query shapes are handled here:

        1. **A key** ("2075", "TC-2075", "SOUSCLOUD-TC-2075") → exact
           ``filter.key`` lookup. Testers search by key constantly and a key
           never appears in the name, so a plain substring search finds nothing.
        2. **One term** → straight ``filter.summary``. Exact totals, one call.
        3. **Several terms** → QMetry can't AND them, and word order would
           otherwise decide whether a search works at all ("delete recipe" found
           6, "recipe delete" found 0). Scan the pages of the *longest* term
           (the most selective one) and AND the rest locally, then page the
           filtered list. `truncated` says the scan hit its cap.

        Returns ``{"total", "rows" (raw), "page_size", "truncated"}``.
        """
        terms = (query or "").split()

        key = self._as_entity_key(query, entity)
        if key:
            data = await self._request(
                "POST",
                path,
                params={"startAt": 0, "maxResults": max_results, "fields": fields},
                json={"filter": {**base_filter, "key": key}},
            )
            raw = data.get("data") or []
            rows = [r for r in raw if not drop(r)]
            return {
                "total": len(rows),
                "page_size": len(raw),
                "rows": rows,
                "truncated": False,
            }

        if len(terms) <= 1:
            flt = {**base_filter}
            if terms:
                flt["summary"] = terms[0]
            data = await self._request(
                "POST",
                path,
                params={
                    "startAt": start_at,
                    "maxResults": max_results,
                    "fields": fields,
                },
                json={"filter": flt},
            )
            raw = data.get("data") or []
            rows = [r for r in raw if not drop(r)]
            return {
                "total": data.get("total", len(rows)),
                "page_size": len(raw),
                "rows": rows,
                "truncated": False,
            }

        # Which term to scan? Ask QMetry how many rows each one matches (one
        # cheap call each, in parallel) and scan the rarest. Guessing by word
        # length instead made "recipe delete" scan ~2x more rows than
        # "delete recipe" for an identical result set.
        totals = await asyncio.gather(
            *(self._term_total(path, base_filter, t, fields) for t in terms)
        )
        fewest = min(totals)
        if fewest == 0:
            # A term nobody matches means the AND can't match either — don't
            # scan anything. Makes a typo'd word fail fast instead of crawling.
            return {"total": 0, "page_size": 0, "rows": [], "truncated": False}
        pivot = terms[totals.index(fewest)]
        scanned, truncated = await self._scan_all(
            path, {**base_filter, "summary": pivot}, fields
        )
        needles = [t.casefold() for t in terms]
        matches = [
            r
            for r in scanned
            if not drop(r)
            and all(
                n in f"{r.get('summary') or ''} {r.get('key') or ''}".casefold()
                for n in needles
            )
        ]
        window = matches[start_at : start_at + max_results]
        return {
            "total": len(matches),
            "page_size": len(window),
            "rows": window,
            "truncated": truncated,
        }

    async def _term_total(
        self, path: str, base_filter: dict[str, Any], term: str, fields: str
    ) -> int:
        """How many rows match one term. One row requested — only the count
        matters. Cached, so it costs nothing on the next keystroke."""
        import time

        ck = (path, term)
        hit = _TERM_TOTAL_CACHE.get(ck)
        if hit and (time.monotonic() - hit[0]) < _SCAN_CACHE_TTL_S:
            return hit[1]
        data = await self._request(
            "POST",
            path,
            params={"startAt": 0, "maxResults": 1, "fields": fields},
            json={"filter": {**base_filter, "summary": term}},
        )
        total = int(data.get("total") or 0)
        _TERM_TOTAL_CACHE[ck] = (time.monotonic(), total)
        return total

    async def _scan_all(
        self, path: str, flt: dict[str, Any], fields: str
    ) -> tuple[list[dict], bool]:
        """Every row matching `flt`, up to a hard page cap, cached briefly.

        Multi-term search has to filter locally, which means it needs the whole
        candidate set rather than one page. The cap stops a very common pivot
        term from turning one keystroke into dozens of calls; the cache means
        typing further terms re-filters the same rows for free.
        """
        import time

        ck = (path, flt.get("summary", ""))
        hit = _SCAN_CACHE.get(ck)
        if hit and (time.monotonic() - hit[0]) < _SCAN_CACHE_TTL_S:
            return hit[1], hit[2]

        async def fetch(page_no: int) -> tuple[list[dict], int]:
            data = await self._request(
                "POST",
                path,
                params={
                    "startAt": page_no * _MAX_RESULTS,
                    "maxResults": _MAX_RESULTS,
                    "fields": fields,
                },
                json={"filter": flt},
            )
            return (data.get("data") or []), data.get("total", 0)

        first, total = await fetch(0)
        rows = list(first)
        # The first page tells us the total, so the rest go out concurrently —
        # fetched one after another this ran ~5s on a common term, long enough
        # to feel like a hang while typing.
        pages_needed = min(
            _MAX_SCAN_PAGES, -(-total // _MAX_RESULTS) if _MAX_RESULTS else 1
        )
        if pages_needed > 1 and first:
            rest = await asyncio.gather(
                *(fetch(n) for n in range(1, pages_needed))
            )
            for page, _ in rest:
                rows.extend(page)
        truncated = len(rows) < total

        _SCAN_CACHE[ck] = (time.monotonic(), rows, truncated)
        return rows, truncated

    def _as_entity_key(self, query: str | None, entity: str) -> str | None:
        """Turn a key-ish query into the full QMetry key, or None.

        Accepts "2075", "tc-2075", "TC 2075", "SOUSCLOUD-TC-2075". QMetry matches
        `filter.key` only on the complete key (live-verified: "TC-2075" alone
        returns nothing), so the project prefix has to be filled in — and if we
        don't know it, there is no key search to offer.
        """
        if not query:
            return None
        m = _KEY_QUERY_RE.match(query)
        if not m:
            return None
        prefix, typed, number = m.group(1), m.group(2), m.group(3)
        if typed and typed.upper() != entity:
            return None  # asked for a TC while browsing TRs, or vice versa
        prefix = prefix or self._project_key
        if not prefix:
            return None
        return f"{prefix.upper()}-{entity}-{number}"

    async def search_test_cases(self, cycle_id: str) -> list[dict[str, Any]]:
        """POST /testcycles/{id}/testcases/search — paginated.

        The real API requires a (non-null) ``filter`` object in the body; an
        empty ``{}`` returns all linked cases. Each entry carries at least:
          id, key, testCaseExecutionId, versionNo, tcvId
        plus ``summary`` and ``precondition``, which only arrive because the
        query asks for them by name (``_CASE_FIELDS``) — that is what removes
        the per-case version-detail call this list used to need.
        """
        results: list[dict] = []
        start_at = 0
        while True:
            data = await self._request(
                "POST",
                f"/testcycles/{cycle_id}/testcases/search",
                params={
                    "startAt": start_at,
                    "maxResults": _MAX_RESULTS,
                    "fields": _CASE_FIELDS,
                },
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

    async def get_test_case_parameters(
        self, tc_id: str, version_no: int
    ) -> list[dict[str, str]]:
        """GET /testcases/{idOrKey}/versions/{no}/parameters.

        Returns ``[{"id", "name", "value"}]``. Two things need this: resolving
        the `[~id]` tokens in step text (via ``params_map``), and the case's own
        test data — QMetry surfaces a parameterised case's parameter table as
        "Test Data". Response shape (live 2026-08-04)::

            [{"rowIndex": 1, "params": [
                {"parameterId": 20322, "parameterName": "User Role",
                 "parameterValueId": 62462, "value": "Admin"}]}]

        Each ``rowIndex`` is one data-driven iteration. Only the first is used —
        the runner executes a case once — and a case with more is logged so the
        skipped iterations are never silent. Returns [] for a case with no
        parameters, which is most of them.
        """
        try:
            resp = await self._request(
                "GET", f"/testcases/{tc_id}/versions/{version_no}/parameters"
            )
        except QMetryError:
            log.warning("Could not load parameters for %s", tc_id, exc_info=True)
            return []
        rows = resp if isinstance(resp, list) else (resp.get("data") or [])
        if not rows:
            return []
        if len(rows) > 1:
            log.info(
                "%s has %d parameter data rows; using rowIndex %s only",
                tc_id, len(rows), rows[0].get("rowIndex"),
            )
        out: list[dict[str, str]] = []
        for p in rows[0].get("params") or []:
            pid = p.get("parameterId")
            if pid is None:
                continue
            out.append({
                "id": str(pid),
                "name": str(p.get("parameterName") or f"Parameter {pid}"),
                "value": str(p.get("value") or ""),
            })
        return out

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
        if is_standalone_plan(plan_key):
            case_key = plan_key[len(STANDALONE_PREFIX) :]
            cases = await self.list_cases(plan_key, with_steps=False)
            name = cases[0]["name"] if cases else case_key
            return {"key": case_key, "name": name}
        cycle = await self._fetch_cycle(plan_key)
        # A cycle's name arrives as ``summary`` (only when the query asks for the
        # field — see get_test_cycle). Fall back to the human key.
        return {
            "key": cycle.get("key", plan_key),
            "name": cycle.get("summary") or cycle.get("key") or plan_key,
        }

    async def list_cases(
        self, plan_key: str, with_steps: bool = True
    ) -> list[dict[str, Any]]:
        """The plan's cases, in QMetry order.

        ``with_steps=False`` returns them without step text — one QMetry call
        for the whole cycle. Steps cost a call each, so the manual console asks
        for the cheap list and hydrates the single case the tester opens (see
        ``get_case_steps``); the orchestrator, which executes every step,
        keeps the eager default. Each case carries ``_steps_loaded`` so callers
        can tell "no steps fetched yet" from "this case genuinely has none".
        """
        cases = await self._base_cases(plan_key)
        if with_steps:
            await self._hydrate_steps(plan_key, cases)
        return cases

    async def get_case_steps(self, plan_key: str, case_id: str) -> list[dict[str, str]]:
        """Load (and cache) one case's steps. Returns [] for an unknown case."""
        cases = await self._base_cases(plan_key)
        match = next((c for c in cases if c["id"] == case_id), None)
        if match is None:
            return []
        await self._hydrate_steps(plan_key, [match])
        return match["steps"]

    async def get_case_test_data(
        self, plan_key: str, case_id: str
    ) -> list[dict[str, str]]:
        """The case's parameter table ("Test Data" in QMetry). Free after
        ``get_case_steps`` — it came back from the same call."""
        cases = await self._base_cases(plan_key)
        match = next((c for c in cases if c["id"] == case_id), None)
        if match is None:
            return []
        await self._hydrate_steps(plan_key, [match])
        return match.get("test_data") or []

    # ------------------------------------------------------------ internals

    async def _base_cases(self, plan_key: str) -> list[dict[str, Any]]:
        """The case list without steps, cached briefly. Any steps already
        fetched for these cases are re-attached from the longer-lived step
        cache so a list refresh never un-loads an open case."""
        import time

        cached = _CASES_CACHE.get(plan_key)
        if cached and (time.monotonic() - cached[0]) < _CASES_CACHE_TTL_S:
            return cached[1]

        if is_standalone_plan(plan_key):
            cases = await self._standalone_case(plan_key)
        else:
            cycle = await self._fetch_cycle(plan_key)
            cycle_id: str = cycle["id"]
            entries = await self._client.search_test_cases(cycle_id)
            cases = []
            for entry in entries:
                tc_id = entry.get("id")
                tc_key = entry.get("key") or str(tc_id)
                cases.append(
                    {
                        "id": tc_key,
                        "name": entry.get("summary") or tc_key,
                        "precondition": clean_step_text(entry.get("precondition")),
                        "steps": [],
                        "test_data": [],
                        "_steps_loaded": False,
                        # Private fields used for writing results back. Real API:
                        # execution id is ``testCaseExecutionId`` (not latestTc…).
                        "_qmetry_cycle_id": cycle_id,
                        "_qmetry_execution_id": entry.get("testCaseExecutionId"),
                        "_qmetry_tc_id": tc_id,
                        "_qmetry_version_no": entry.get("versionNo", 1),
                    }
                )

        for case in cases:
            remembered = _STEPS_CACHE.get((plan_key, case["id"]))
            if remembered is not None:
                case["steps"] = remembered
                case["_steps_loaded"] = True
                case["test_data"] = _CASE_TEST_DATA_CACHE.get(
                    (plan_key, case["id"]), []
                )

        _CASES_CACHE[plan_key] = (time.monotonic(), cases)
        return cases

    async def _standalone_case(self, plan_key: str) -> list[dict[str, Any]]:
        """The single case behind a ``TC:<idOrKey>`` plan.

        There is no cycle and so no execution id — results from a standalone
        case are never written back to QMetry, which the console reflects by
        hiding its push control.
        """
        case_key = plan_key[len(STANDALONE_PREFIX) :]
        version_no = 1
        try:
            versions = await self._client.get_test_case_versions(case_key)
            latest = next(
                (v for v in versions if v.get("isLatestVersion")),
                versions[0] if versions else {},
            )
            version_no = latest.get("versionNo", 1)
        except QMetryError:
            log.warning("Could not load versions for %s", case_key, exc_info=True)

        name, precondition, tc_id = case_key, "", case_key
        try:
            detail = await self._client.get_test_case_version_detail(
                case_key, version_no
            )
            name = detail.get("summary") or case_key
            precondition = clean_step_text(detail.get("precondition"))
            tc_id = detail.get("id") or case_key
        except QMetryError:
            log.warning("Could not load detail for %s", case_key, exc_info=True)

        return [
            {
                "id": case_key,
                "name": name,
                "precondition": precondition,
                "steps": [],
                "test_data": [],
                "_steps_loaded": False,
                "_qmetry_cycle_id": None,
                "_qmetry_execution_id": None,
                "_qmetry_tc_id": tc_id,
                "_qmetry_version_no": version_no,
            }
        ]

    async def _hydrate_steps(
        self, plan_key: str, cases: list[dict[str, Any]]
    ) -> None:
        """Fetch steps for any of `cases` that don't have them yet, in place."""
        pending = [c for c in cases if not c.get("_steps_loaded")]
        if not pending:
            return
        # One QMetry call per case — bounded concurrency, or a big cycle takes
        # minutes end to end.
        sem = asyncio.Semaphore(_CASE_FETCH_CONCURRENCY)

        async def _one(case: dict) -> None:
            tc_id = case["_qmetry_tc_id"]
            version_no = case["_qmetry_version_no"]
            async with sem:
                # Fetched once, used twice: to resolve `[~id]` tokens in the step
                # text, and as the case's own test data — QMetry surfaces a
                # parameterised case's parameter table as "Test Data".
                parameters = await self._client.get_test_case_parameters(
                    tc_id, version_no
                )
                steps = await self._load_steps(
                    tc_id, version_no, case["id"], params=params_map(parameters)
                )
            case["steps"] = steps
            case["_steps_loaded"] = True
            case["test_data"] = [
                {"name": p["name"], "value": p["value"]} for p in parameters
            ]
            _STEPS_CACHE[(plan_key, case["id"])] = steps
            _CASE_TEST_DATA_CACHE[(plan_key, case["id"])] = case["test_data"]

        await asyncio.gather(*(_one(c) for c in pending))

    async def _fetch_cycle(self, cycle_key: str) -> dict:
        if cycle_key not in self._cycle_cache:
            self._cycle_cache[cycle_key] = await self._client.get_test_cycle(
                cycle_key
            )
        return self._cycle_cache[cycle_key]

    async def _load_steps(
        self,
        tc_id: str,
        version_no: int,
        tc_key: str,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """Load + flatten a case's steps for the given version.

        The API returns steps in order. A step is either a plain step (with
        ``stepDetails`` / ``expectedResult``) or a *shareable* reference whose
        real steps live under ``shareable.shareableTestSteps`` — expand those
        inline so the tester sees a single flat list.

        Parameters are fetched alongside so `[~id]` tokens become their real
        values. Shareable steps are exactly where those tokens live (the same
        shared "Login to Sous Chef Website" step serves every role, with the
        role as a parameter), so resolving them is what makes a shared step
        readable per case.
        """
        try:
            raw_steps = await self._client.get_test_steps(tc_id, version_no)
        except QMetryError:
            log.warning("Could not load steps for %s", tc_key, exc_info=True)
            return []

        if params is None:
            params = params_map(
                await self._client.get_test_case_parameters(tc_id, version_no)
            )

        def _one(step: dict) -> dict[str, str]:
            """One flat step.

            ``test_data`` — the inputs the tester is meant to enter — stays its
            own field rather than being appended to the action text, so the
            console can label it per step. Only some steps have any.
            `Orchestrator` re-joins the two when building the model's prompt.
            """
            return {
                "action": clean_step_text(step.get("stepDetails"), params),
                "expected": clean_step_text(step.get("expectedResult"), params),
                "test_data": clean_step_text(step.get("testData"), params),
            }

        flat: list[dict[str, str]] = []
        for s in raw_steps:
            if s.get("stepDetails"):
                flat.append(_one(s))
                continue
            shareable = s.get("shareable") or {}
            for sub in shareable.get("shareableTestSteps", []):
                flat.append(_one(sub))
        return flat
