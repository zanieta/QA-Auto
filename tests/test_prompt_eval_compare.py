"""Offline verification for scripts/prompt_eval/compare_combinations.py.

No live Azure calls: `agent.azure_ai.httpx.AsyncClient` is monkeypatched to a
fake class so AzureAIClient's own network path (`_get_client()` constructing a
real httpx.AsyncClient) never runs — matching the mocking pattern already used
in tests/test_azure_ai.py, just applied at the class level since
compare_combinations.py builds its own AzureAIClient() per combo rather than
accepting an injected http_client.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scripts.prompt_eval.compare_combinations import (
    ComboResult,
    judge_combo,
    parse_combo,
)

REPO = Path(__file__).resolve().parent.parent
PAYLOAD_PATH = REPO / "scripts" / "prompt_eval" / "eval_input_tc2_step4.json"


def _ok(content: str) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient: queues canned responses, never touches the network."""

    _queue: list[MagicMock] = []

    def __init__(self, *a, **kw):
        pass

    async def post(self, *a, **kw):
        return _FakeAsyncClient._queue.pop(0)

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _no_live_azure(monkeypatch):
    """Every test in this file is guaranteed offline: AsyncClient is fully replaced."""
    monkeypatch.setattr("agent.azure_ai.httpx.AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient._queue = []
    yield
    _FakeAsyncClient._queue = []


@pytest.fixture()
def payload() -> dict:
    return json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))


def test_parse_combo_happy_path():
    assert parse_combo("gpt-4.1:result_evaluator_41.txt") == ("gpt-4.1", "result_evaluator_41.txt")


def test_parse_combo_rejects_missing_colon():
    with pytest.raises(Exception):
        parse_combo("gpt-4.1-result_evaluator_41.txt")


@pytest.mark.asyncio
async def test_judge_combo_reports_distribution_and_no_flip(payload, monkeypatch):
    _FakeAsyncClient._queue = [
        _ok('{"status":"pass","reason":"ok 1"}'),
        _ok('{"status":"pass","reason":"ok 2"}'),
        _ok('{"status":"pass","reason":"ok 3"}'),
    ]
    result = await judge_combo(payload, "gpt-4o", "result_evaluator.txt", n=3)
    assert result.statuses == ["pass", "pass", "pass"]
    assert result.counts == {"pass": 3}
    assert result.flip_rate == 0.0
    assert result.modal_status == "pass"


@pytest.mark.asyncio
async def test_judge_combo_detects_flip_rate(payload):
    """The exact failure mode this harness exists to catch: identical input, different verdicts."""
    _FakeAsyncClient._queue = [
        _ok('{"status":"pass","reason":"looks fine"}'),
        _ok('{"status":"fail","reason":"missing element"}'),
        _ok('{"status":"pass","reason":"looks fine"}'),
    ]
    result = await judge_combo(payload, "gpt-4.1-mini", "result_evaluator_41.txt", n=3)
    assert result.counts == {"pass": 2, "fail": 1}
    assert result.modal_status == "pass"
    assert result.flip_rate == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_disagreement_with_baseline(payload):
    _FakeAsyncClient._queue = [_ok('{"status":"pass","reason":"a"}')] * 2
    baseline = await judge_combo(payload, "gpt-4o", "result_evaluator.txt", n=2)

    _FakeAsyncClient._queue = [
        _ok('{"status":"fail","reason":"b"}'),
        _ok('{"status":"pass","reason":"c"}'),
    ]
    candidate = await judge_combo(payload, "gpt-4.1", "result_evaluator_41.txt", n=2)

    assert baseline.modal_status == "pass"
    assert candidate.disagreement_with(baseline) == pytest.approx(0.5)


def test_combo_result_label():
    r = ComboResult(deployment="gpt-4.1", prompt_file="result_evaluator_41.txt")
    assert r.label == "gpt-4.1 x result_evaluator_41.txt"


@pytest.mark.asyncio
async def test_judge_combo_never_touches_real_network(payload):
    """If real httpx.AsyncClient were used, this would hang/error with no queued response
    and no real credentials; success here proves the fake fully replaced it."""
    _FakeAsyncClient._queue = [_ok('{"status":"blocked","reason":"setup not shown"}')]
    result = await judge_combo(payload, "gpt-4.1", "result_evaluator_41.txt", n=1)
    assert result.statuses == ["blocked"]
