"""Tests for the QMetry client."""

from __future__ import annotations

import re

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.qmetry import QMetryClient, QMetryError, QMetryCaseSource, clean_step_text


@pytest.fixture(autouse=True)
def _clear_cases_cache():
    """The module-level caches must never leak between tests."""
    from agent import qmetry as _qm

    for cache in (
        _qm._CASES_CACHE, _qm._STEPS_CACHE, _qm._SCAN_CACHE, _qm._TERM_TOTAL_CACHE,
    ):
        cache.clear()
    yield
    for cache in (
        _qm._CASES_CACHE, _qm._STEPS_CACHE, _qm._SCAN_CACHE, _qm._TERM_TOTAL_CACHE,
    ):
        cache.clear()


# ---------------------------------------------------------------- unit tests


def test_auth_header_is_api_key():
    client = QMetryClient(api_key="abc123", project_id="10022")
    assert client._headers["apiKey"] == "abc123"
    assert "Authorization" not in client._headers


def test_base_url_is_qtmcloud():
    from agent.qmetry import _BASE

    assert "qtmcloud.qmetry.com" in _BASE
    assert "/rest/api/latest" in _BASE


# ---------------------------------------------------------------- async tests


def _mock_response(json_data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.content = b"data"
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
        resp.text = str(json_data)
    return resp


@pytest.mark.asyncio
async def test_get_test_cycle_returns_data():
    cycle = {"id": "CY-1", "key": "PROJ-CY-1", "summary": "Sprint 1"}
    client = QMetryClient(api_key="key", project_id="10022")
    with patch.object(client, "_request", new=AsyncMock(return_value=cycle)):
        result = await client.get_test_cycle("CY-1")
    assert result["summary"] == "Sprint 1"


@pytest.mark.asyncio
async def test_get_test_cycle_raises_qmetry_error_on_client_error():
    client = QMetryClient(api_key="key", project_id="10022")
    with patch.object(
        client, "_request", new=AsyncMock(side_effect=QMetryError("401"))
    ):
        with pytest.raises(QMetryError, match="401"):
            await client.get_test_cycle("CY-1")


@pytest.mark.asyncio
async def test_search_test_cases_returns_list():
    page = {
        "total": 1,
        "data": [
            {"id": "tc1", "key": "P-TC-1", "summary": "Test 1", "latestTcExecutionId": 100}
        ],
    }
    client = QMetryClient(api_key="key", project_id="10022")
    with patch.object(client, "_request", new=AsyncMock(return_value=page)):
        result = await client.search_test_cases("CY-1")
    assert len(result) == 1
    assert result[0]["key"] == "P-TC-1"


@pytest.mark.asyncio
async def test_get_test_case_versions_returns_list():
    versions = [
        {"versionNo": 1, "isLatestVersion": False},
        {"versionNo": 2, "isLatestVersion": True},
    ]
    client = QMetryClient(api_key="key", project_id="10022")
    with patch.object(client, "_request", new=AsyncMock(return_value=versions)):
        result = await client.get_test_case_versions("tc1")
    assert result == versions


@pytest.mark.asyncio
async def test_get_test_steps_returns_raw_data():
    page = {
        "total": 2,
        "data": [
            {"id": 2, "seqNo": 2, "stepDetails": "Step B", "expectedResult": "B ok"},
            {"id": 1, "seqNo": 1, "stepDetails": "Step A", "expectedResult": "A ok"},
        ],
    }
    client = QMetryClient(api_key="key", project_id="10022")
    with patch.object(client, "_request", new=AsyncMock(return_value=page)):
        result = await client.get_test_steps("tc1", 2)
    # Raw order from API â€” sorting is the caller's responsibility
    assert len(result) == 2
    assert {s["stepDetails"] for s in result} == {"Step A", "Step B"}


@pytest.mark.asyncio
async def test_post_execution_result_skips_unknown_status():
    client = QMetryClient(api_key="key", project_id="10022")
    exec_results = [{"name": "Pass", "id": 1}, {"name": "Fail", "id": 2}]
    mock_request = AsyncMock(return_value=exec_results)
    with patch.object(client, "_request", new=mock_request):
        # "blocked" not in the list â†’ should silently skip, not call PUT
        client._exec_result_cache = {"pass": 1, "fail": 2}
        await client.post_execution_result("CY-1", 99, "blocked")
    mock_request.assert_not_called()


@pytest.mark.asyncio
async def test_get_test_step_executions_returns_ordered_rows():
    page = {
        "total": 2,
        "data": [
            {"testStepExecutionId": 501, "testStepSeqNo": 1},
            {"testStepExecutionId": 502, "testStepSeqNo": 2},
        ],
    }
    client = QMetryClient(api_key="key", project_id="10022")
    with patch.object(client, "_request", new=AsyncMock(return_value=page)):
        rows = await client.get_test_step_executions("CY-1", 100)
    # testStepExecutionId is exposed as id for callers
    assert [r["id"] for r in rows] == [501, 502]


@pytest.mark.asyncio
async def test_post_step_execution_result_puts_with_result_id():
    client = QMetryClient(api_key="key", project_id="10022")
    client._exec_result_cache = {"pass": 1, "fail": 2, "blocked": 3}
    mock_request = AsyncMock(return_value={})
    with patch.object(client, "_request", new=mock_request):
        await client.post_step_execution_result("CY-1", 100, 501, "pass", "looks good")
    args, kwargs = mock_request.call_args
    assert args[0] == "PUT"
    assert "teststeps/501" in args[1]
    assert kwargs["json"]["executionResultId"] == 1
    assert kwargs["json"]["comment"] == "looks good"


@pytest.mark.asyncio
async def test_post_step_execution_result_skips_unknown_status():
    client = QMetryClient(api_key="key", project_id="10022")
    client._exec_result_cache = {"pass": 1, "fail": 2}
    mock_request = AsyncMock(return_value={})
    with patch.object(client, "_request", new=mock_request):
        await client.post_step_execution_result("CY-1", 100, 501, "blocked")
    mock_request.assert_not_called()


@pytest.mark.asyncio
async def test_create_execution_returns_new_exec_id():
    client = QMetryClient(api_key="key", project_id="10022")
    resp = {"data": {"id": 909, "tcId": "tc1"}}
    mock_request = AsyncMock(return_value=resp)
    with patch.object(client, "_request", new=mock_request):
        new_id = await client.create_execution("CY-1", "tc1", 2)
    assert new_id == 909
    args, kwargs = mock_request.call_args
    assert args[0] == "POST"
    assert args[1] == "/testcycles/CY-1/testcase-executions"


# ---------------------------------------------------------------- QMetryCaseSource


@pytest.mark.asyncio
async def test_case_source_get_plan():
    cycle = {"id": "CY-1", "key": "PROJ-CY-1", "summary": "Sprint 1"}
    source = QMetryCaseSource(QMetryClient(api_key="key", project_id="10022"))
    with patch.object(source._client, "_request", new=AsyncMock(return_value=cycle)):
        plan = await source.get_plan("CY-1")
    assert plan["key"] == "PROJ-CY-1"
    assert plan["name"] == "Sprint 1"


@pytest.mark.asyncio
async def test_get_test_cycle_unwraps_data():
    """Real API wraps the cycle under a ``data`` key â€” get_test_cycle unwraps it."""
    wrapped = {"data": {"id": "CY-1", "key": "PROJ-CY-1", "archived": False}}
    client = QMetryClient(api_key="key", project_id="10022")
    with patch.object(client, "_request", new=AsyncMock(return_value=wrapped)):
        result = await client.get_test_cycle("CY-1")
    assert result["id"] == "CY-1"
    assert result["key"] == "PROJ-CY-1"


@pytest.mark.asyncio
async def test_case_source_list_cases_shape():
    # Real API shapes: cycle wrapped under "data"; search rows carry
    # testCaseExecutionId + versionNo; the case name lives on the version detail.
    cycle = {"data": {"id": "CY-1", "key": "PROJ-CY-1"}}
    tc_page = {
        "total": 1,
        "data": [
            {
                "id": "tc1",
                "key": "P-TC-1",
                "summary": "Create item",
                "testCaseExecutionId": 100,
                "versionNo": 1,
            }
        ],
    }
    steps_page = {
        "total": 1,
        "data": [{"seqNo": 1, "stepDetails": "Click New", "expectedResult": "Form opens"}],
    }
    paths: list[str] = []

    async def _request(method, path, **_kw):
        paths.append(path)
        if path.endswith("/parameters"):
            return []
        if "teststeps/search" in path:
            return steps_page
        if "testcases/search" in path:
            return tc_page
        return cycle

    source = QMetryCaseSource(QMetryClient(api_key="key", project_id="10022"))
    with patch.object(source._client, "_request", new=AsyncMock(side_effect=_request)):
        cases = await source.list_cases("CY-1")

    assert len(cases) == 1
    assert cases[0]["id"] == "P-TC-1"
    # The name rides along on the cycle's case search — no per-case detail call.
    assert cases[0]["name"] == "Create item"
    assert not any(re.search(r"/versions/\d+$", p) for p in paths)
    assert cases[0]["steps"][0]["action"] == "Click New"
    assert cases[0]["steps"][0]["expected"] == "Form opens"
    assert cases[0]["_qmetry_execution_id"] == 100
    assert cases[0]["_qmetry_cycle_id"] == "CY-1"


@pytest.mark.asyncio
async def test_load_steps_flattens_shareable():
    """A shareable step expands into its nested shareableTestSteps."""
    cycle = {"data": {"id": "CY-1", "key": "PROJ-CY-1"}}
    tc_page = {
        "total": 1,
        "data": [
            {
                "id": "tc1",
                "key": "P-TC-1",
                "summary": "Shareable case",
                "testCaseExecutionId": 7,
                "versionNo": 1,
            }
        ],
    }
    steps_page = {
        "total": 2,
        "data": [
            {
                "seqNo": 1,
                "stepDetails": None,
                "shareable": {
                    "shareableTestSteps": [
                        {"seqNo": "1.1", "stepDetails": "Open login", "expectedResult": "Login shows"},
                        {"seqNo": "1.2", "stepDetails": "Enter creds", "expectedResult": "Logged in"},
                    ]
                },
            },
            {"seqNo": 2, "stepDetails": "Check sidebar", "expectedResult": "Menus shown"},
        ],
    }

    async def _request(method, path, **_kw):
        if path.endswith("/parameters"):
            return []
        if "teststeps/search" in path:
            return steps_page
        if "testcases/search" in path:
            return tc_page
        return cycle

    source = QMetryCaseSource(QMetryClient(api_key="key", project_id="10022"))
    with patch.object(source._client, "_request", new=AsyncMock(side_effect=_request)):
        cases = await source.list_cases("CY-1")

    steps = cases[0]["steps"]
    # 2 expanded shareable sub-steps + 1 plain step = 3 flat steps
    assert [s["action"] for s in steps] == ["Open login", "Enter creds", "Check sidebar"]
    assert steps[0]["expected"] == "Login shows"
    assert steps[2]["expected"] == "Menus shown"


# ----- step-text cleaning ---------------------------------------------------


def test_clean_step_text_strips_jira_wiki_markup():
    raw = (
        "h4. Navigate to the Sous Chef Cloud\n\n"
        "# Open an internet browser: *Google Chrome*.\n"
        "#* *Test Server* {{https://test.souscheftech.com/login}}\n"
        "{panel:bgColor=#eae6ff}\nh3. Log in as a user\n{panel}\n\n\n\n"
        "!https://example.com/pic.png|width=300!"
    )
    out = clean_step_text(raw)
    assert "h4." not in out
    assert "{panel" not in out
    assert "{{" not in out
    assert "*Google Chrome*" not in out and "Google Chrome" in out
    assert "!https://" not in out and "[image]" in out
    assert "\n\n\n" not in out


def test_clean_step_text_replaces_mentions():
    out = clean_step_text('From the main navigation menu, locate the "[~22720]" menu.')
    assert "[~22720]" not in out
    assert "[unresolved reference]" in out


def test_clean_step_text_resolves_parameters():
    """`[~id]` tokens are test-case PARAMETERS, not user mentions. Left
    unresolved they sent the agent to the wrong page and failed 14 of 26 steps
    in TC-1985 (2026-07-14)."""
    raw = 'From the main navigation menu, locate the "[~22720]" menu and select the "[~22788]" sub-menu.'
    out = clean_step_text(raw, {"22720": "Recipe", "22788": "Edit Inventory"})
    assert out == (
        'From the main navigation menu, locate the "Recipe" menu and select the '
        '"Edit Inventory" sub-menu.'
    )
    assert "unresolved" not in out


def test_clean_step_text_marks_only_the_parameters_it_cannot_resolve():
    out = clean_step_text('Role "[~20322]" opens the "[~99999]" menu', {"20322": "Admin"})
    assert '"Admin"' in out
    assert "[~99999]" not in out
    assert "[unresolved reference]" in out


def test_clean_step_text_ignores_empty_parameter_values():
    """A parameter defined but left blank must not silently delete the token —
    an empty substitution would read as if nothing was ever referenced."""
    out = clean_step_text('the "[~20322]" menu', {"20322": ""})
    assert "[unresolved reference]" in out


@pytest.mark.asyncio
async def test_get_test_case_parameters_maps_ids_to_values():
    client = QMetryClient(api_key="k", project_id="10022")
    client._request = AsyncMock(return_value=[
        {"rowIndex": 1, "params": [
            {"parameterId": 20322, "parameterName": "User Role",
             "parameterValueId": 62462, "value": "Admin"},
            {"parameterId": 22720, "parameterName": "Menu", "value": "Recipe"},
        ]},
    ])
    params = await client.get_test_case_parameters("SOUSCLOUD-TC-2", 1)
    # Names come back too — the case-level "Test data" block renders them.
    assert params == [
        {"id": "20322", "name": "User Role", "value": "Admin"},
        {"id": "22720", "name": "Menu", "value": "Recipe"},
    ]
    from agent.qmetry import params_map
    assert params_map(params) == {"20322": "Admin", "22720": "Recipe"}
    args, _kwargs = client._request.call_args
    assert args[1] == "/testcases/SOUSCLOUD-TC-2/versions/1/parameters"


@pytest.mark.asyncio
async def test_get_test_case_parameters_uses_the_first_data_row_only():
    """Extra rowIndex values are data-driven iterations; the runner executes a
    case once, so only the first is used."""
    client = QMetryClient(api_key="k", project_id="10022")
    client._request = AsyncMock(return_value=[
        {"rowIndex": 1, "params": [{"parameterId": 1, "value": "first"}]},
        {"rowIndex": 2, "params": [{"parameterId": 1, "value": "second"}]},
    ])
    rows = await client.get_test_case_parameters("TC-1", 1)
    assert [r["value"] for r in rows] == ["first"]


@pytest.mark.asyncio
async def test_get_test_case_parameters_tolerates_no_parameters():
    client = QMetryClient(api_key="k", project_id="10022")
    client._request = AsyncMock(return_value=[])
    assert await client.get_test_case_parameters("TC-1", 1) == []

    client._request = AsyncMock(side_effect=QMetryError("boom"))
    assert await client.get_test_case_parameters("TC-1", 1) == []


@pytest.mark.asyncio
async def test_load_steps_resolves_parameters_in_shareable_steps():
    """Shareable steps are exactly where the tokens live — one shared "log in"
    step serves every role, with the role as a parameter."""
    client = MagicMock()
    client.get_test_steps = AsyncMock(return_value=[
        {
            "seqNo": 1, "stepDetails": None,
            "shareable": {"shareableTestSteps": [
                {"seqNo": "1.1",
                 "stepDetails": 'Log in as a user with "[~20322]" access.',
                 "expectedResult": 'The "[~20322]" role menus are visible.',
                 "testData": 'Role: [~20322]'},
            ]},
        },
        {"seqNo": 2, "stepDetails": 'Open the "[~22720]" menu',
         "expectedResult": "It opens"},
    ])
    client.get_test_case_parameters = AsyncMock(return_value=[
        {"id": "20322", "name": "User Role", "value": "Access Manager"},
        {"id": "22720", "name": "Menu", "value": "Recipe"},
    ])
    source = QMetryCaseSource(client=client)
    steps = await source._load_steps("tc1", 1, "TC-1")

    assert steps[0]["action"] == 'Log in as a user with "Access Manager" access.'
    assert steps[0]["expected"] == 'The "Access Manager" role menus are visible.'
    assert steps[0]["test_data"] == "Role: Access Manager"
    assert steps[1]["action"] == 'Open the "Recipe" menu'
    client.get_test_case_parameters.assert_awaited_once_with("tc1", 1)


def test_clean_step_text_handles_none_and_empty():
    assert clean_step_text(None) == ""
    assert clean_step_text("") == ""
    assert clean_step_text("plain text") == "plain text"



def test_load_steps_includes_test_data(monkeypatch):
    client = MagicMock()
    client.get_test_steps = AsyncMock(return_value=[
        {"stepDetails": "Enter the cook time", "expectedResult": "Value accepted",
         "testData": "Cook Mode Time: {{45}}"},
        {"stepDetails": "Click Save", "expectedResult": "Saved", "testData": ""},
    ])
    client.get_test_case_parameters = AsyncMock(return_value=[])
    source = QMetryCaseSource(client=client)
    import asyncio as _a
    steps = _a.run(source._load_steps("tc1", 1, "TC-1"))
    # test_data is its OWN field, not appended to the action: the console labels
    # it per step (and shows "none" when absent). The orchestrator re-joins them
    # when it builds the model's prompt.
    assert steps[0]["action"] == "Enter the cook time"
    assert steps[0]["test_data"] == "Cook Mode Time: 45"
    assert steps[1]["test_data"] == ""
    assert steps[1]["action"] == "Click Save"


def test_clean_step_text_preserves_list_depth():
    out = clean_step_text("* Menus:\n** Recipe\n*** Edit Inventory\n** Logout")
    assert out.splitlines() == [
        "• Menus:",
        "  - Recipe",
        "    - Edit Inventory",
        "  - Logout",
    ]


@pytest.mark.asyncio
async def test_list_test_cycles_returns_unarchived_keys():
    client = QMetryClient(api_key="k", project_id="10022")
    client._request = AsyncMock(return_value={
        "startAt": 0, "maxResults": 50, "total": 3,
        "data": [
            {"id": "aaa", "key": "SOUSCLOUD-TR-490", "archived": False},
            {"id": "bbb", "key": "SOUSCLOUD-TR-489", "archived": True},
            {"id": "ccc", "key": "SOUSCLOUD-TR-488", "archived": False},
        ],
    })
    cycles = await client.list_test_cycles(max_results=50)
    # No `summary` in these rows, so the name falls back to the human key.
    assert cycles == [
        {"id": "aaa", "key": "SOUSCLOUD-TR-490", "name": "SOUSCLOUD-TR-490"},
        {"id": "ccc", "key": "SOUSCLOUD-TR-488", "name": "SOUSCLOUD-TR-488"},
    ]
    args, kwargs = client._request.call_args
    assert args[0] == "POST" and args[1] == "/testcycles/search"
    assert kwargs["json"] == {"filter": {"projectId": "10022", "archived": False}}


@pytest.mark.asyncio
async def test_search_test_cycles_returns_names_and_total():
    """Cycles DO have a name — it arrives as `summary`, but only when the query
    names the field (live 2026-08-04). `query` filters QMetry-side."""
    client = QMetryClient(api_key="k", project_id="10022")
    client._request = AsyncMock(return_value={
        "total": 430,
        "data": [
            {"id": "aaa", "key": "SOUSCLOUD-TR-434", "summary": "Full Regression — Alex"},
        ],
    })
    page = await client.search_test_cycles(query="regression", start_at=50, max_results=25)
    assert page["total"] == 430
    assert page["rows"] == [
        {"id": "aaa", "key": "SOUSCLOUD-TR-434", "name": "Full Regression — Alex"}
    ]
    _args, kwargs = client._request.call_args
    # Archived cycles are excluded BY QMETRY, so `total` and the page agree and
    # the caller's offset can't drift off the server's.
    assert kwargs["json"] == {
        "filter": {"projectId": "10022", "archived": False, "summary": "regression"}
    }
    assert kwargs["params"]["startAt"] == 50
    assert kwargs["params"]["maxResults"] == 25
    assert "summary" in kwargs["params"]["fields"]


@pytest.mark.asyncio
async def test_search_project_test_cases_pages_and_drops_noise():
    """Project-wide library search: archived cases and shareable step
    containers are not executable, so they never reach the picker."""
    client = QMetryClient(api_key="k", project_id="10022")
    client._request = AsyncMock(return_value={
        "total": 2534,
        "data": [
            {
                "id": "a", "key": "P-TC-1", "summary": "Login page",
                "precondition": "# One *bold*", "version": {"versionNo": 3},
            },
            {"id": "b", "key": "P-TC-2", "summary": "Archived", "archived": True},
            {"id": "c", "key": "P-TC-3", "summary": "Shared steps", "shareable": True},
        ],
    })
    page = await client.search_project_test_cases(query="login", start_at=0, max_results=50)
    assert page["total"] == 2534
    assert [r["key"] for r in page["rows"]] == ["P-TC-1"]
    assert page["rows"][0]["name"] == "Login page"
    assert page["rows"][0]["version_no"] == 3
    assert page["rows"][0]["precondition"] == clean_step_text("# One *bold*")
    # 3 rows came back but only 1 is usable — a caller must still advance by 3
    # or the next page would repeat what this one already skipped.
    assert page["page_size"] == 3
    _args, kwargs = client._request.call_args
    assert kwargs["json"] == {"filter": {"projectId": "10022", "summary": "login"}}


def _rows(*items: tuple[str, str]) -> list[dict]:
    return [{"id": k.lower(), "key": k, "summary": s} for k, s in items]


@pytest.mark.asyncio
async def test_search_is_word_order_independent():
    """QMetry's filter is one substring with no AND, so "delete recipe" found 6
    cases and "recipe delete" found 0. The client ANDs the terms itself."""
    client = QMetryClient(api_key="k", project_id="10022", project_key="P")
    library = _rows(
        ("P-TC-1", "Delete a Recipe"),
        ("P-TC-2", "Recipe list — delete disabled"),
        ("P-TC-3", "Recipe list only"),
        ("P-TC-4", "Delete a user"),
    )

    async def _request(_method, _path, params=None, json=None):
        term = (json["filter"].get("summary") or "").casefold()
        hits = [r for r in library if term in r["summary"].casefold()]
        start = (params or {}).get("startAt", 0)
        size = (params or {}).get("maxResults", 100)
        return {"total": len(hits), "data": hits[start : start + size]}

    with patch.object(client, "_request", new=AsyncMock(side_effect=_request)):
        forward = await client.search_project_test_cases("delete recipe")
        reverse = await client.search_project_test_cases("recipe delete")

    assert [r["key"] for r in forward["rows"]] == ["P-TC-1", "P-TC-2"]
    assert [r["key"] for r in reverse["rows"]] == ["P-TC-1", "P-TC-2"]
    assert forward["total"] == reverse["total"] == 2


@pytest.mark.asyncio
async def test_multi_term_search_scans_the_rarest_term():
    """The scanned term decides how many rows get pulled — pick the one QMetry
    says matches fewest, not the longest word."""
    client = QMetryClient(api_key="k", project_id="10022", project_key="P")
    totals = {"recipe": 900, "delete": 20}
    scanned: list[str] = []

    async def _request(_method, _path, params=None, json=None):
        term = json["filter"].get("summary") or ""
        if (params or {}).get("maxResults") == 1:
            return {"total": totals.get(term, 0), "data": []}
        scanned.append(term)
        return {"total": 1, "data": _rows(("P-TC-1", "Delete a Recipe"))}

    with patch.object(client, "_request", new=AsyncMock(side_effect=_request)):
        page = await client.search_project_test_cases("recipe delete")

    assert scanned == ["delete"]  # the rarer term, not the first or longest
    assert [r["key"] for r in page["rows"]] == ["P-TC-1"]


@pytest.mark.asyncio
async def test_multi_term_search_fails_fast_when_a_term_matches_nothing():
    """A typo in one word can't AND with anything — don't scan at all."""
    client = QMetryClient(api_key="k", project_id="10022", project_key="P")
    calls: list[dict] = []

    async def _request(_method, _path, params=None, json=None):
        calls.append(params or {})
        term = json["filter"].get("summary") or ""
        return {"total": 0 if term == "zzzq" else 500, "data": []}

    with patch.object(client, "_request", new=AsyncMock(side_effect=_request)):
        page = await client.search_project_test_cases("recipe zzzq")

    assert page == {"total": 0, "page_size": 0, "rows": [], "truncated": False}
    # Only the cheap 1-row probes went out — no page scan.
    assert all(c.get("maxResults") == 1 for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    ["SOUSCLOUD-TC-2075", "TC-2075", "tc 2075", "tc-2075", "2075", " 2075 "],
)
async def test_key_shaped_queries_do_an_exact_key_lookup(query):
    """A key never appears in the name, so a substring search finds nothing.
    Every shorthand has to expand to the one key QMetry matches on."""
    client = QMetryClient(api_key="k", project_id="10022", project_key="SOUSCLOUD")
    mock = AsyncMock(return_value={
        "total": 1, "data": _rows(("SOUSCLOUD-TC-2075", "Delete recipe")),
    })
    with patch.object(client, "_request", new=mock):
        page = await client.search_project_test_cases(query)
    assert page["total"] == 1
    _args, kwargs = mock.call_args
    assert kwargs["json"]["filter"]["key"] == "SOUSCLOUD-TC-2075"
    assert "summary" not in kwargs["json"]["filter"]


@pytest.mark.asyncio
async def test_key_query_for_the_other_entity_is_not_a_key_lookup():
    """"TR-434" while browsing test cases is not a case key — fall through to a
    normal search rather than inventing SOUSCLOUD-TC-434."""
    client = QMetryClient(api_key="k", project_id="10022", project_key="SOUSCLOUD")
    mock = AsyncMock(return_value={"total": 0, "data": []})
    with patch.object(client, "_request", new=mock):
        await client.search_project_test_cases("TR-434")
    _args, kwargs = mock.call_args
    assert "key" not in kwargs["json"]["filter"]
    assert kwargs["json"]["filter"]["summary"] == "TR-434"


@pytest.mark.asyncio
async def test_cycle_key_query_resolves_against_the_run_entity():
    client = QMetryClient(api_key="k", project_id="10022", project_key="SOUSCLOUD")
    mock = AsyncMock(return_value={
        "total": 1, "data": _rows(("SOUSCLOUD-TR-434", "Full Regression")),
    })
    with patch.object(client, "_request", new=mock):
        page = await client.search_test_cycles("434")
    assert page["rows"][0]["key"] == "SOUSCLOUD-TR-434"
    _args, kwargs = mock.call_args
    assert kwargs["json"]["filter"]["key"] == "SOUSCLOUD-TR-434"


@pytest.mark.asyncio
async def test_key_lookup_needs_a_project_key():
    """Without a project prefix a bare number can't be expanded, so it stays a
    plain search instead of guessing."""
    client = QMetryClient(api_key="k", project_id="10022", project_key="")
    import os

    mock = AsyncMock(return_value={"total": 0, "data": []})
    with patch.dict(os.environ, {"JIRA_PROJECT_KEY": ""}, clear=False):
        client._project_key = ""
        with patch.object(client, "_request", new=mock):
            await client.search_project_test_cases("2075")
    _args, kwargs = mock.call_args
    assert "key" not in kwargs["json"]["filter"]
    assert kwargs["json"]["filter"]["summary"] == "2075"


@pytest.mark.asyncio
async def test_multi_term_scan_is_capped_and_says_so():
    """A pivot term matching more than the scan cap makes `total` a floor, and
    the caller has to be told rather than shown a confident wrong number."""
    from agent import qmetry as qm

    client = QMetryClient(api_key="k", project_id="10022", project_key="P")
    huge = qm._MAX_SCAN_PAGES * qm._MAX_RESULTS + 500

    async def _request(_method, _path, params=None, json=None):
        if (params or {}).get("maxResults") == 1:
            return {"total": huge, "data": []}
        return {
            "total": huge,
            "data": _rows(*[(f"P-TC-{i}", "alpha beta") for i in range(100)]),
        }

    with patch.object(client, "_request", new=AsyncMock(side_effect=_request)):
        page = await client.search_project_test_cases("alpha beta")

    assert page["truncated"] is True
    assert page["total"] == qm._MAX_SCAN_PAGES * qm._MAX_RESULTS


@pytest.mark.asyncio
async def test_multi_term_paging_slices_the_filtered_list():
    client = QMetryClient(api_key="k", project_id="10022", project_key="P")
    library = _rows(*[(f"P-TC-{i}", f"alpha beta {i}") for i in range(12)])

    async def _request(_method, _path, params=None, json=None):
        if (params or {}).get("maxResults") == 1:
            return {"total": len(library), "data": []}
        return {"total": len(library), "data": library}

    with patch.object(client, "_request", new=AsyncMock(side_effect=_request)):
        p1 = await client.search_project_test_cases("alpha beta", 0, 5)
        p2 = await client.search_project_test_cases("alpha beta", 5, 5)

    assert p1["total"] == p2["total"] == 12
    assert p1["page_size"] == 5
    assert not ({r["key"] for r in p1["rows"]} & {r["key"] for r in p2["rows"]})


@pytest.mark.asyncio
async def test_get_test_cycle_asks_for_the_name_field():
    """A cycle GET without `fields` returns no summary at all (live-verified),
    which is why the run header used to show only the key."""
    client = QMetryClient(api_key="k", project_id="10022")
    mock_request = AsyncMock(return_value={"data": {"id": "c1", "summary": "Smoke"}})
    with patch.object(client, "_request", new=mock_request):
        cycle = await client.get_test_cycle("c1")
    assert cycle["summary"] == "Smoke"
    _args, kwargs = mock_request.call_args
    assert "summary" in kwargs["params"]["fields"]


@pytest.mark.asyncio
async def test_version_detail_requests_precondition_field():
    """fields=all does NOT return precondition (verified live) — the query
    must explicitly name ``precondition`` alongside ``summary``."""
    client = QMetryClient(api_key="key", project_id="10022")
    mock_request = AsyncMock(return_value={"data": {"summary": "Case"}})
    with patch.object(client, "_request", new=mock_request):
        await client.get_test_case_version_detail("tc1", 2)
    args, kwargs = mock_request.call_args
    assert args[0] == "GET"
    assert args[1] == "/testcases/tc1/versions/2"
    assert kwargs["params"] == {"fields": "summary,precondition"}


@pytest.mark.asyncio
async def test_list_cases_carries_cleaned_precondition():
    cycle = {"data": {"id": "CY-1", "key": "PROJ-CY-1"}}
    tc_page = {
        "total": 2,
        "data": [
            {
                "id": "tc1", "key": "P-TC-1", "summary": "Case",
                "precondition": "# One\n# Two *bold*",
                "testCaseExecutionId": 100, "versionNo": 1,
            },
            {
                "id": "tc2", "key": "P-TC-2", "summary": "Case two",
                "testCaseExecutionId": 101, "versionNo": 1,
            },
        ],
    }
    steps_page = {
        "total": 1,
        "data": [{"seqNo": 1, "stepDetails": "Click New", "expectedResult": "Form opens"}],
    }

    async def _request(method, path, **_kw):
        if path.endswith("/parameters"):
            return []
        if "teststeps/search" in path:
            return steps_page
        if "testcases/search" in path:
            return tc_page
        return cycle

    source = QMetryCaseSource(QMetryClient(api_key="key", project_id="10022"))
    with patch.object(source._client, "_request", new=AsyncMock(side_effect=_request)):
        cases = await source.list_cases("CY-1")

    assert cases[0]["precondition"] == clean_step_text("# One\n# Two *bold*")
    assert cases[1]["precondition"] == ""


@pytest.mark.asyncio
async def test_list_cases_is_cached_within_ttl(monkeypatch):
    """Marking a case refreshes the UI — that must not re-crawl QMetry."""
    from agent import qmetry as qm

    qm._CASES_CACHE.clear()
    client = MagicMock()
    client.get_test_cycle = AsyncMock(return_value={"id": "cyc1", "key": "TR-1"})
    client.search_test_cases = AsyncMock(return_value=[
        {"id": "tc1", "key": "TC-1", "summary": "Case one", "versionNo": 1,
         "testCaseExecutionId": 9},
    ])
    client.get_test_steps = AsyncMock(return_value=[
        {"stepDetails": "do it", "expectedResult": "done"},
    ])
    client.get_test_case_parameters = AsyncMock(return_value=[])
    source = QMetryCaseSource(client=client)

    first = await source.list_cases("TR-1")
    second = await source.list_cases("TR-1")
    assert first == second
    assert client.search_test_cases.await_count == 1  # served from cache

    qm._CASES_CACHE.clear()
    qm._STEPS_CACHE.clear()
    await source.list_cases("TR-1")
    assert client.search_test_cases.await_count == 2  # cache cleared -> refetch


@pytest.mark.asyncio
async def test_list_cases_without_steps_makes_one_call_for_the_whole_cycle():
    """The slow part of opening a run was one steps call per case. The console
    asks for the cheap list and hydrates only the case the tester opens."""
    client = MagicMock()
    client.get_test_cycle = AsyncMock(return_value={"id": "cyc1", "key": "TR-1"})
    client.search_test_cases = AsyncMock(return_value=[
        {"id": "t1", "key": "TC-1", "summary": "One", "versionNo": 1,
         "testCaseExecutionId": 1},
        {"id": "t2", "key": "TC-2", "summary": "Two", "versionNo": 1,
         "testCaseExecutionId": 2},
    ])
    client.get_test_steps = AsyncMock(return_value=[
        {"stepDetails": "do it", "expectedResult": "done"},
    ])
    client.get_test_case_parameters = AsyncMock(return_value=[])
    source = QMetryCaseSource(client=client)

    cases = await source.list_cases("TR-1", with_steps=False)
    assert [c["name"] for c in cases] == ["One", "Two"]
    assert all(c["steps"] == [] and c["_steps_loaded"] is False for c in cases)
    assert client.get_test_steps.await_count == 0

    steps = await source.get_case_steps("TR-1", "TC-2")
    assert steps[0]["action"] == "do it"
    assert client.get_test_steps.await_count == 1  # only the case that was opened

    # Re-listing must not un-load the hydrated case, or an open case would go
    # blank every time the tester saves a mark.
    again = await source.list_cases("TR-1", with_steps=False)
    opened = next(c for c in again if c["id"] == "TC-2")
    assert opened["_steps_loaded"] is True and opened["steps"] == steps
    assert client.get_test_steps.await_count == 1


@pytest.mark.asyncio
async def test_case_test_data_is_the_parameter_table_and_costs_no_extra_call():
    """QMetry surfaces a parameterised case's parameter table as "Test Data".
    It arrives with the same call that resolves the step tokens, so exposing it
    per case must not cost a second round trip."""
    client = MagicMock()
    client.get_test_cycle = AsyncMock(return_value={"id": "cyc1", "key": "TR-1"})
    client.search_test_cases = AsyncMock(return_value=[
        {"id": "t1", "key": "TC-1", "summary": "One", "versionNo": 1,
         "testCaseExecutionId": 1},
    ])
    client.get_test_steps = AsyncMock(return_value=[
        {"stepDetails": 'Log in as "[~20322]"', "expectedResult": "ok"},
    ])
    client.get_test_case_parameters = AsyncMock(return_value=[
        {"id": "20322", "name": "User Role", "value": "Admin"},
    ])
    source = QMetryCaseSource(client=client)

    steps = await source.get_case_steps("TR-1", "TC-1")
    assert steps[0]["action"] == 'Log in as "Admin"'
    test_data = await source.get_case_test_data("TR-1", "TC-1")
    assert test_data == [{"name": "User Role", "value": "Admin"}]
    assert client.get_test_case_parameters.await_count == 1

    # And it survives a list refresh, like steps do.
    again = await source.list_cases("TR-1", with_steps=False)
    assert again[0]["test_data"] == [{"name": "User Role", "value": "Admin"}]
    assert client.get_test_case_parameters.await_count == 1


@pytest.mark.asyncio
async def test_case_with_no_parameters_has_empty_test_data():
    client = MagicMock()
    client.get_test_cycle = AsyncMock(return_value={"id": "cyc1", "key": "TR-1"})
    client.search_test_cases = AsyncMock(return_value=[
        {"id": "t1", "key": "TC-1", "summary": "One", "versionNo": 1,
         "testCaseExecutionId": 1},
    ])
    client.get_test_steps = AsyncMock(return_value=[
        {"stepDetails": "Click Save", "expectedResult": "Saved"},
    ])
    client.get_test_case_parameters = AsyncMock(return_value=[])
    source = QMetryCaseSource(client=client)
    assert await source.get_case_test_data("TR-1", "TC-1") == []
    assert await source.get_case_test_data("TR-1", "NOPE") == []


@pytest.mark.asyncio
async def test_get_case_steps_is_idempotent():
    client = MagicMock()
    client.get_test_cycle = AsyncMock(return_value={"id": "cyc1", "key": "TR-1"})
    client.search_test_cases = AsyncMock(return_value=[
        {"id": "t1", "key": "TC-1", "summary": "One", "versionNo": 1,
         "testCaseExecutionId": 1},
    ])
    client.get_test_steps = AsyncMock(return_value=[
        {"stepDetails": "do it", "expectedResult": "done"},
    ])
    client.get_test_case_parameters = AsyncMock(return_value=[])
    source = QMetryCaseSource(client=client)
    await source.get_case_steps("TR-1", "TC-1")
    await source.get_case_steps("TR-1", "TC-1")
    assert client.get_test_steps.await_count == 1
    assert await source.get_case_steps("TR-1", "NOPE") == []


@pytest.mark.asyncio
async def test_standalone_plan_is_one_case_with_no_execution():
    """TC mode opens a case straight from the project library. There is no
    cycle behind it, so there is no execution id to write results into."""
    from agent.qmetry import is_standalone_plan, standalone_plan_key

    plan = standalone_plan_key("SOUSCLOUD-TC-2")
    assert plan == "TC:SOUSCLOUD-TC-2" and is_standalone_plan(plan)

    client = MagicMock()
    client.get_test_case_versions = AsyncMock(return_value=[
        {"versionNo": 1, "isLatestVersion": False},
        {"versionNo": 4, "isLatestVersion": True},
    ])
    client.get_test_case_version_detail = AsyncMock(return_value={
        "id": "internal9", "summary": "Login page", "precondition": "# Be logged out",
    })
    client.get_test_steps = AsyncMock(return_value=[
        {"stepDetails": "Open the app", "expectedResult": "Login shows"},
    ])
    client.get_test_case_parameters = AsyncMock(return_value=[])
    source = QMetryCaseSource(client=client)

    meta = await source.get_plan(plan)
    assert meta == {"key": "SOUSCLOUD-TC-2", "name": "Login page"}

    cases = await source.list_cases(plan)
    assert len(cases) == 1
    case = cases[0]
    assert case["id"] == "SOUSCLOUD-TC-2"
    assert case["precondition"] == clean_step_text("# Be logged out")
    assert case["steps"][0]["action"] == "Open the app"
    assert case["_qmetry_execution_id"] is None
    assert case["_qmetry_cycle_id"] is None
    # The latest version is the one executed, not version 1.
    assert case["_qmetry_version_no"] == 4
    client.get_test_cycle.assert_not_called()


@pytest.mark.asyncio
async def test_write_case_execution_edit_maps_steps_by_position():
    from agent.qmetry import write_case_execution

    client = MagicMock()
    client.get_test_step_executions = AsyncMock(return_value=[
        {"id": 501}, {"id": 502}, {"id": 503},
    ])
    client.post_step_execution_result = AsyncMock(return_value=None)
    client.post_execution_result = AsyncMock(return_value=None)
    client.create_execution = AsyncMock()

    result = await write_case_execution(
        client,
        cycle_id="CY-1", execution_id=100, tc_id="tc1", version_no=1,
        case_status="fail",
        step_results={0: ("pass", None), 2: ("fail", "broke")},
        mode="edit", comment="case note",
    )

    client.create_execution.assert_not_awaited()
    assert result.exec_id == 100
    assert result.steps_written == 2
    calls = {c.args[2]: (c.args[3], c.args[4]) for c in client.post_step_execution_result.await_args_list}
    assert calls[501] == ("pass", None)
    assert calls[503] == ("fail", "broke")
    assert 502 not in calls
    ca, ck = client.post_execution_result.await_args
    assert ck.get("status", ca[2] if len(ca) > 2 else None) == "fail"


@pytest.mark.asyncio
async def test_write_case_execution_create_uses_new_exec_id():
    from agent.qmetry import write_case_execution

    client = MagicMock()
    client.create_execution = AsyncMock(return_value=909)
    client.get_test_step_executions = AsyncMock(return_value=[{"id": 700}])
    client.post_step_execution_result = AsyncMock(return_value=None)
    client.post_execution_result = AsyncMock(return_value=None)

    result = await write_case_execution(
        client,
        cycle_id="CY-1", execution_id=100, tc_id="tc1", version_no=1,
        case_status="pass",
        step_results={0: ("pass", None)},
        mode="create",
    )

    client.create_execution.assert_awaited_once_with("CY-1", "tc1", 1)
    assert result.exec_id == 909
    assert client.get_test_step_executions.await_args.args == ("CY-1", 909)
    assert client.post_step_execution_result.await_args.args[1] == 909


@pytest.mark.asyncio
async def test_write_case_execution_step_error_is_non_fatal():
    from agent.qmetry import write_case_execution, QMetryError

    client = MagicMock()
    client.create_execution = AsyncMock()
    client.get_test_step_executions = AsyncMock(return_value=[{"id": 501}, {"id": 502}])
    async def _post_step(cycle_id, exec_id, step_exec_id, status, comment=None):
        if step_exec_id == 501:
            raise QMetryError("boom")
    client.post_step_execution_result = AsyncMock(side_effect=_post_step)
    client.post_execution_result = AsyncMock(return_value=None)

    result = await write_case_execution(
        client,
        cycle_id="CY-1", execution_id=100, tc_id="tc1", version_no=1,
        case_status="pass",
        step_results={0: ("pass", None), 1: ("pass", None)},
        mode="edit",
    )
    assert result.steps_written == 1
    assert len(result.errors) == 1
    assert result.errors[0]["step_exec_id"] == 501
    client.post_execution_result.assert_awaited_once()
