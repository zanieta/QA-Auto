"""Tests for the QMetry client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.qmetry import QMetryClient, QMetryError, QMetryCaseSource, clean_step_text


@pytest.fixture(autouse=True)
def _clear_cases_cache():
    """The module-level cases cache must never leak between tests."""
    from agent import qmetry as _qm

    _qm._CASES_CACHE.clear()
    yield
    _qm._CASES_CACHE.clear()


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
            {"id": "tc1", "key": "P-TC-1", "testCaseExecutionId": 100, "versionNo": 1}
        ],
    }
    version_detail = {"data": {"id": "tc1", "key": "P-TC-1", "summary": "Create item"}}
    steps_page = {
        "total": 1,
        "data": [{"seqNo": 1, "stepDetails": "Click New", "expectedResult": "Form opens"}],
    }

    async def _request(method, path, **_kw):
        if "teststeps/search" in path:
            return steps_page
        if "testcases/search" in path:
            return tc_page
        if "/versions/" in path:
            return version_detail
        return cycle

    source = QMetryCaseSource(QMetryClient(api_key="key", project_id="10022"))
    with patch.object(source._client, "_request", new=AsyncMock(side_effect=_request)):
        cases = await source.list_cases("CY-1")

    assert len(cases) == 1
    assert cases[0]["id"] == "P-TC-1"
    assert cases[0]["name"] == "Create item"  # from version detail summary
    assert cases[0]["steps"][0]["action"] == "Click New"
    assert cases[0]["steps"][0]["expected"] == "Form opens"
    assert cases[0]["_qmetry_execution_id"] == 100
    assert cases[0]["_qmetry_cycle_id"] == "CY-1"


@pytest.mark.asyncio
async def test_load_steps_flattens_shareable():
    """A shareable step expands into its nested shareableTestSteps."""
    cycle = {"data": {"id": "CY-1", "key": "PROJ-CY-1"}}
    tc_page = {"total": 1, "data": [{"id": "tc1", "key": "P-TC-1", "testCaseExecutionId": 7, "versionNo": 1}]}
    version_detail = {"data": {"summary": "Shareable case"}}
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
        if "teststeps/search" in path:
            return steps_page
        if "testcases/search" in path:
            return tc_page
        if "/versions/" in path:
            return version_detail
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
    source = QMetryCaseSource(client=client)
    import asyncio as _a
    steps = _a.run(source._load_steps("tc1", 1, "TC-1"))
    assert steps[0]["action"] == "Enter the cook time\nTest data: Cook Mode Time: 45"
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
    assert cycles == [
        {"id": "aaa", "key": "SOUSCLOUD-TR-490"},
        {"id": "ccc", "key": "SOUSCLOUD-TR-488"},
    ]
    args, kwargs = client._request.call_args
    assert args[0] == "POST" and args[1] == "/testcycles/search"
    assert kwargs["json"] == {"filter": {"projectId": "10022"}}


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
            {"id": "tc1", "key": "P-TC-1", "testCaseExecutionId": 100, "versionNo": 1},
            {"id": "tc2", "key": "P-TC-2", "testCaseExecutionId": 101, "versionNo": 1},
        ],
    }
    steps_page = {
        "total": 1,
        "data": [{"seqNo": 1, "stepDetails": "Click New", "expectedResult": "Form opens"}],
    }

    async def _request(method, path, **_kw):
        if "teststeps/search" in path:
            return steps_page
        if "testcases/search" in path:
            return tc_page
        if "/versions/" in path and "tc1" in path:
            return {"data": {"summary": "Case", "precondition": "# One\n# Two *bold*"}}
        if "/versions/" in path and "tc2" in path:
            return {"data": {"summary": "Case two"}}
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
        {"id": "tc1", "key": "TC-1", "versionNo": 1, "testCaseExecutionId": 9},
    ])
    client.get_test_case_version_detail = AsyncMock(return_value={"summary": "Case one"})
    client.get_test_steps = AsyncMock(return_value=[
        {"stepDetails": "do it", "expectedResult": "done"},
    ])
    source = QMetryCaseSource(client=client)

    first = await source.list_cases("TR-1")
    second = await source.list_cases("TR-1")
    assert first == second
    assert client.search_test_cases.await_count == 1  # served from cache

    qm._CASES_CACHE.clear()
    await source.list_cases("TR-1")
    assert client.search_test_cases.await_count == 2  # cache cleared -> refetch


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
