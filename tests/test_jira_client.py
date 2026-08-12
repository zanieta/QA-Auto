"""Jira client tests — httpx mocked. Verifies basic auth, body shape,
ADF wrapping, retry on 429/5xx, and the bugs_from_failed_run helper.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agent.jira_client import (
    JiraClient,
    JiraError,
    _adf_paragraph,
    bugs_from_failed_run,
)
from agent.run_state import Step, TestCase, new_run_state


def _client(responses):
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(side_effect=list(responses))
    fake.get = AsyncMock()
    c = JiraClient(
        base_url="https://duke.atlassian.net",
        email="r@duke",
        api_token="t",
        project_key="SOUSCLOUD",
        http_client=fake,
    )
    return c, fake


def _ok(payload=None):
    r = MagicMock(spec=httpx.Response)
    r.status_code = 201
    r.content = b'{"x":1}'
    r.json.return_value = payload or {"id": "10000", "key": "SOUSCLOUD-1"}
    return r


def _err(code, body="boom", headers=None):
    r = MagicMock(spec=httpx.Response)
    r.status_code = code
    r.text = body
    r.headers = headers or {}
    return r


# ---- auth + shape ---------------------------------------------------------


def test_basic_auth_token_is_email_colon_token_b64():
    c = JiraClient(
        base_url="https://duke.atlassian.net",
        email="a@b",
        api_token="t",
        project_key="X",
    )
    decoded = base64.b64decode(c._basic_token).decode()
    assert decoded == "a@b:t"
    assert c._headers["Authorization"].startswith("Basic ")


def test_adf_paragraph_shape():
    doc = _adf_paragraph("hello")
    assert doc["type"] == "doc"
    assert doc["version"] == 1
    assert doc["content"][0]["type"] == "paragraph"
    assert doc["content"][0]["content"][0] == {"type": "text", "text": "hello"}


# ---- create_bug body shape -----------------------------------------------


@pytest.mark.asyncio
async def test_create_bug_posts_correct_body():
    c, fake = _client([_ok({"key": "SOUSCLOUD-99"})])
    res = await c.create_bug(
        summary="X failed",
        description="ran into Y",
        labels=["qa-agent"],
    )
    assert res == {"key": "SOUSCLOUD-99"}
    args, kwargs = fake.post.call_args
    assert args[0] == "https://duke.atlassian.net/rest/api/3/issue"
    body = kwargs["json"]
    assert body["fields"]["project"]["key"] == "SOUSCLOUD"
    assert body["fields"]["issuetype"]["name"] == "Bug"
    assert body["fields"]["summary"] == "X failed"
    assert body["fields"]["description"]["type"] == "doc"  # ADF
    assert body["fields"]["labels"] == ["qa-agent"]


@pytest.mark.asyncio
async def test_add_comment_uses_adf_body():
    c, fake = _client([_ok({"id": "1"})])
    await c.add_comment("SOUSCLOUD-1", "more context")
    body = fake.post.call_args.kwargs["json"]
    assert body["body"]["type"] == "doc"  # ADF
    url = fake.post.call_args.args[0]
    assert url.endswith("/rest/api/3/issue/SOUSCLOUD-1/comment")


# ---- retries --------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_on_429(monkeypatch):
    c, fake = _client([_err(429, "rate", {"retry-after": "0"}), _ok()])
    monkeypatch.setattr("agent.jira_client.asyncio.sleep", AsyncMock())
    await c.create_bug("s", "d")
    assert fake.post.await_count == 2


@pytest.mark.asyncio
async def test_raises_after_max_attempts(monkeypatch):
    c, fake = _client([_err(500), _err(500), _err(500)])
    monkeypatch.setattr("agent.jira_client.asyncio.sleep", AsyncMock())
    with pytest.raises(JiraError):
        await c.create_bug("s", "d")
    assert fake.post.await_count == 3


@pytest.mark.asyncio
async def test_404_fails_fast(monkeypatch):
    c, fake = _client([_err(404, "no such issue")])
    monkeypatch.setattr("agent.jira_client.asyncio.sleep", AsyncMock())
    with pytest.raises(JiraError):
        await c.add_comment("SOUSCLOUD-1", "hi")
    assert fake.post.await_count == 1


# ---- bugs_from_failed_run helper -----------------------------------------


def test_bugs_from_failed_run_skips_non_failures():
    state = new_run_state("X", "Plan X")
    state.add_case(TestCase(id="A", name="alpha", status="pass"))
    state.add_case(TestCase(id="B", name="bravo", status="fail", steps=[
        Step(action="Click", detail="click #x", status="fail",
             evaluation="No dialog", duration_seconds=2.5),
    ]))
    state.add_case(TestCase(id="C", name="charlie", status="blocked"))
    bugs = bugs_from_failed_run(state)
    assert len(bugs) == 1
    assert bugs[0]["summary"].startswith("[QA Agent] B — bravo")
    assert "No dialog" in bugs[0]["description"]
    assert "Plan X" in bugs[0]["description"]


def test_bugs_from_failed_run_includes_step_test_data():
    """Slice 3 separated test_data from step.action; the Jira bug must still
    say WHAT to type, not just the verb, or the bug is unactionable."""
    state = new_run_state("X", "Plan X")
    state.add_case(TestCase(id="B", name="bravo", status="fail", steps=[
        Step(action="Fill recipe name", detail="fill [data-test=recipe-name]",
             status="fail", evaluation="Field rejected input",
             duration_seconds=1.0, test_data="Grilled Salmon"),
    ]))
    bugs = bugs_from_failed_run(state)
    assert len(bugs) == 1
    assert "Test data: Grilled Salmon" in bugs[0]["description"]


def test_bugs_from_failed_run_omits_test_data_line_when_absent():
    state = new_run_state("X", "Plan X")
    state.add_case(TestCase(id="B", name="bravo", status="fail", steps=[
        Step(action="Click", detail="click #x", status="fail",
             evaluation="No dialog", duration_seconds=2.5),
    ]))
    bugs = bugs_from_failed_run(state)
    assert "Test data:" not in bugs[0]["description"]
