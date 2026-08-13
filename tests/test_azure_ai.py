"""Tests for the Azure OpenAI client.

httpx is mocked so the suite never hits the network. Verifies:
  - chat URL + headers are constructed per Azure's spec
  - translate_step parses both bare-list and {"actions": [...]} outputs
  - evaluate_result rejects bad status values
  - retry kicks in on 429 / 5xx / transport errors and stops at max_attempts
  - 4xx other than 429 fails fast
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agent.azure_ai import (
    AzureAIClient,
    AzureAIError,
    PROMPTS_DIR,
    _parse_actions,
    _parse_evaluation,
)

ENDPOINT = "https://x.openai.azure.com"
KEY = "k"
DEPLOY = "gpt-4o"


@pytest.fixture(autouse=True)
def _isolate_azure_env(monkeypatch):
    """server.py load_dotenv()s the real .env at import (pytest collection),
    which leaks the developer's deployment overrides into these tests."""
    for var in (
        "AZURE_AI_TRANSLATOR_DEPLOYMENT",
        "AZURE_AI_EVALUATOR_DEPLOYMENT",
        "AZURE_AI_API_VERSION",
        "EVALUATOR_PROMPT_FILE",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------- file shape


def test_prompts_directory_exists():
    assert PROMPTS_DIR.is_dir()
    assert (PROMPTS_DIR / "step_translator.txt").is_file()
    assert (PROMPTS_DIR / "result_evaluator.txt").is_file()


def test_chat_url_format():
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY)
    assert c._chat_url == (
        f"{ENDPOINT}/openai/deployments/{DEPLOY}"
        f"/chat/completions?api-version={c.api_version}"
    )


def test_auth_header_uses_api_key():
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY)
    assert c._headers["api-key"] == KEY
    assert "Authorization" not in c._headers


def test_role_deployments_default_to_base_deployment():
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY)
    assert c.translator_deployment == DEPLOY
    assert c.evaluator_deployment == DEPLOY


def test_role_deployments_env_fallback(monkeypatch):
    monkeypatch.setenv("AZURE_AI_TRANSLATOR_DEPLOYMENT", "gpt-4.1-mini")
    monkeypatch.delenv("AZURE_AI_EVALUATOR_DEPLOYMENT", raising=False)
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY)
    assert c.translator_deployment == "gpt-4.1-mini"
    assert c.evaluator_deployment == DEPLOY


# ---------------------------------------------------------------- evaluator prompt file


def test_evaluator_prompt_file_defaults_to_result_evaluator_txt():
    """Unset EVALUATOR_PROMPT_FILE -> byte-identical behaviour to before this feature."""
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY)
    assert c.evaluator_prompt_file == "result_evaluator.txt"


@pytest.mark.asyncio
async def test_evaluator_prompt_file_default_loads_result_evaluator_txt(monkeypatch):
    """The default path really reads prompts/result_evaluator.txt, not a copy."""
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(return_value=_ok('{"status":"pass","reason":"ok"}'))
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    await c.evaluate_result("frame", "Expected X")
    body = fake.post.call_args.kwargs["json"]
    system_prompt = body["messages"][0]["content"]
    assert system_prompt == (PROMPTS_DIR / "result_evaluator.txt").read_text(encoding="utf-8")


def test_evaluator_prompt_file_env_override(monkeypatch):
    monkeypatch.setenv("EVALUATOR_PROMPT_FILE", "result_evaluator_41.txt")
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY)
    assert c.evaluator_prompt_file == "result_evaluator_41.txt"


def test_evaluator_prompt_file_constructor_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv("EVALUATOR_PROMPT_FILE", "result_evaluator_41.txt")
    c = AzureAIClient(
        endpoint=ENDPOINT,
        api_key=KEY,
        deployment=DEPLOY,
        evaluator_prompt_file="result_evaluator.txt",
    )
    assert c.evaluator_prompt_file == "result_evaluator.txt"


@pytest.mark.asyncio
async def test_evaluator_prompt_file_override_is_actually_used(monkeypatch):
    monkeypatch.setenv("EVALUATOR_PROMPT_FILE", "result_evaluator_41.txt")
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(return_value=_ok('{"status":"pass","reason":"ok"}'))
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    await c.evaluate_result("frame", "Expected X")
    body = fake.post.call_args.kwargs["json"]
    system_prompt = body["messages"][0]["content"]
    assert system_prompt == (PROMPTS_DIR / "result_evaluator_41.txt").read_text(encoding="utf-8")
    assert system_prompt != (PROMPTS_DIR / "result_evaluator.txt").read_text(encoding="utf-8")


def test_evaluator_prompt_file_missing_override_fails_loudly_at_construction(monkeypatch):
    """A missing/unreadable override must raise, never silently fall back."""
    monkeypatch.setenv("EVALUATOR_PROMPT_FILE", "does_not_exist.txt")
    with pytest.raises(AzureAIError):
        AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY)


@pytest.mark.asyncio
async def test_reasoning_model_temperature_rejection_is_retried_without_it():
    """gpt-5.x deployments reject `temperature`; the client drops it and retries."""
    reject = _err(400, '{"error":{"message":"Unsupported parameter: temperature"}}')
    ok = _ok(json.dumps([{"action": "click", "selector": "#x", "value": None}]))
    client, fake = _client_with([reject, ok, ok])
    await client.translate_step("Click X")
    # first call sent temperature, retry did not
    first_body = fake.post.call_args_list[0].kwargs["json"]
    second_body = fake.post.call_args_list[1].kwargs["json"]
    assert "temperature" in first_body
    assert "temperature" not in second_body
    # deployment is remembered â€” the next call skips temperature entirely
    await client.translate_step("Click X again")
    third_body = fake.post.call_args_list[2].kwargs["json"]
    assert "temperature" not in third_body


@pytest.mark.asyncio
async def test_translate_and_evaluate_hit_their_own_deployments():
    action = json.dumps([{"action": "click", "selector": "#x", "value": None}])
    evaluation = '{"status":"pass","reason":"ok"}'
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(side_effect=[_ok(action), _ok(evaluation)])
    c = AzureAIClient(
        endpoint=ENDPOINT,
        api_key=KEY,
        deployment=DEPLOY,
        translator_deployment="gpt-4.1-mini",
        evaluator_deployment="gpt-4o-vision",
        http_client=fake,
    )
    await c.translate_step("Click X")
    await c.evaluate_result("aW1n", "X is clicked")
    urls = [call.args[0] for call in fake.post.call_args_list]
    assert "/deployments/gpt-4.1-mini/" in urls[0]
    assert "/deployments/gpt-4o-vision/" in urls[1]


# ---------------------------------------------------------------- parsers


def test_parse_actions_bare_list():
    out, done = _parse_actions(json.dumps(
        [{"action": "click", "selector": "#go", "value": None}]
    ))
    assert out == [{"action": "click", "ref": None, "selector": "#go", "value": None}]
    assert done is False


def test_parse_actions_object_wrapper():
    out, _done = _parse_actions(json.dumps(
        {"actions": [{"action": "navigate", "selector": "/inventory", "value": None}]}
    ))
    assert out[0]["action"] == "navigate"


def test_parse_actions_strips_code_fence():
    raw = "```json\n[{\"action\":\"click\",\"selector\":\"#x\",\"value\":null}]\n```"
    out, _done = _parse_actions(raw)
    assert out[0]["action"] == "click"


def test_parse_actions_rejects_unknown_action():
    with pytest.raises(AzureAIError):
        _parse_actions(json.dumps([{"action": "explode", "selector": "x"}]))


def test_parse_actions_rejects_non_list():
    with pytest.raises(AzureAIError):
        _parse_actions(json.dumps({"foo": "bar"}))


def test_parse_evaluation_happy():
    assert _parse_evaluation('{"status":"pass","reason":"all good"}') == {
        "status": "pass",
        "reason": "all good",
    }


def test_parse_evaluation_rejects_bad_status():
    with pytest.raises(AzureAIError):
        _parse_evaluation('{"status":"maybe","reason":"x"}')


# ---------------------------------------------------------------- http behavior


def _ok(content: str) -> MagicMock:
    """Build a fake httpx.Response with a JSON chat-completion payload."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _err(status: int, body: str = "boom", headers: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = body
    resp.headers = headers or {}
    return resp


def _client_with(responses):
    """AzureAIClient whose underlying httpx client returns the given queue of responses."""
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(side_effect=list(responses))
    return (
        AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake),
        fake,
    )


@pytest.mark.asyncio
async def test_translate_step_happy_path():
    payload = json.dumps(
        [{"action": "navigate", "selector": "/inventory/recipes", "value": None}]
    )
    client, fake = _client_with([_ok(payload)])
    actions = await client.translate_step("Navigate to Inventory")
    assert actions == [
        {"action": "navigate", "ref": None, "selector": "/inventory/recipes", "value": None}
    ]
    # called the right URL with the right headers
    args, kwargs = fake.post.call_args
    assert args[0] == client._chat_url
    assert kwargs["headers"]["api-key"] == KEY
    # body asked for JSON output
    body = kwargs["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_evaluate_result_attaches_image_as_data_url():
    client, fake = _client_with([_ok('{"status":"fail","reason":"button missing"}')])
    result = await client.evaluate_result("AAAA", "Save button visible")
    assert result == {"status": "fail", "reason": "button missing"}
    body = fake.post.call_args.kwargs["json"]
    user = body["messages"][1]["content"]
    image = next(p for p in user if p["type"] == "image_url")
    assert image["image_url"]["url"] == "data:image/png;base64,AAAA"


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds(monkeypatch):
    payload = json.dumps([{"action": "click", "selector": "#x", "value": None}])
    client, fake = _client_with([_err(429, "rate", {"retry-after": "0"}), _ok(payload)])
    # don't actually sleep
    monkeypatch.setattr("agent.azure_ai.asyncio.sleep", AsyncMock())
    actions = await client.translate_step("Click X")
    assert actions[0]["action"] == "click"
    assert fake.post.await_count == 2


@pytest.mark.asyncio
async def test_retries_on_500_then_succeeds(monkeypatch):
    payload = json.dumps([{"action": "click", "selector": "#x", "value": None}])
    client, fake = _client_with([_err(503), _err(502), _ok(payload)])
    monkeypatch.setattr("agent.azure_ai.asyncio.sleep", AsyncMock())
    actions = await client.translate_step("Click X")
    assert actions[0]["action"] == "click"
    assert fake.post.await_count == 3


@pytest.mark.asyncio
async def test_raises_after_max_attempts(monkeypatch):
    client, fake = _client_with([_err(500), _err(500), _err(500)])
    monkeypatch.setattr("agent.azure_ai.asyncio.sleep", AsyncMock())
    with pytest.raises(AzureAIError):
        await client.translate_step("Click X")
    assert fake.post.await_count == 3


@pytest.mark.asyncio
async def test_400_fails_fast(monkeypatch):
    client, fake = _client_with([_err(400, "bad request")])
    monkeypatch.setattr("agent.azure_ai.asyncio.sleep", AsyncMock())
    with pytest.raises(AzureAIError):
        await client.translate_step("Click X")
    assert fake.post.await_count == 1  # no retry on 4xx other than 429


@pytest.mark.asyncio
async def test_retries_on_transport_error(monkeypatch):
    payload = json.dumps([{"action": "click", "selector": "#x", "value": None}])
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(
        side_effect=[httpx.ConnectError("dns"), _ok(payload)]
    )
    client = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    monkeypatch.setattr("agent.azure_ai.asyncio.sleep", AsyncMock())
    actions = await client.translate_step("Click X")
    assert actions[0]["action"] == "click"
    assert fake.post.await_count == 2


@pytest.mark.asyncio
async def test_translate_step_rejects_empty_action_list(monkeypatch):
    client, fake = _client_with([_ok("[]")])
    monkeypatch.setattr("agent.azure_ai.asyncio.sleep", AsyncMock())
    with pytest.raises(AzureAIError):
        await client.translate_step("Click X")


# --- DOM-grounded translate ---------------------------------------------------
import json as _json
from unittest.mock import AsyncMock as _AsyncMock

from agent.azure_ai import AzureAIClient as _Client, _parse_actions as _pa


def test_parse_actions_passes_ref_through():
    raw = _json.dumps({"actions": [{"action": "click", "ref": "e7", "value": None}]})
    out, _done = _pa(raw)
    assert out == [{"action": "click", "ref": "e7", "selector": None, "value": None}]


@pytest.mark.asyncio
async def test_translate_step_includes_elements_in_prompt():
    client = _Client(endpoint="https://x", api_key="k", deployment="gpt-4o")
    captured = {}

    async def fake_chat(messages, **kw):
        captured["messages"] = messages
        return _json.dumps({"actions": [{"action": "click", "ref": "e2", "value": None}]})

    client._chat = fake_chat  # type: ignore
    elements = [
        {"ref": "e2", "tag": "a", "role": "link", "name": "Logout"},
        {"ref": "e3", "tag": "input", "role": "", "name": "Email"},
    ]
    actions = await client.translate_step("Click Logout", app_context="url: /x", elements=elements)
    assert actions[0]["ref"] == "e2"
    user_msg = captured["messages"][-1]["content"]
    assert "e2" in user_msg and "Logout" in user_msg  # element list reached the prompt


@pytest.mark.asyncio
async def test_translate_step_works_without_elements():
    client = _Client(endpoint="https://x", api_key="k", deployment="gpt-4o")

    async def fake_chat(messages, **kw):
        return _json.dumps({"actions": [{"action": "navigate", "value": "/home"}]})

    client._chat = fake_chat  # type: ignore
    actions = await client.translate_step("Go home", app_context="dry-run mode")
    assert actions[0]["action"] == "navigate"
    assert actions[0]["value"] == "/home"


@pytest.mark.asyncio
async def test_evaluate_result_accepts_multiple_screenshots():
    """A list of frames becomes one ordered image_url block per frame."""
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(return_value=_ok('{"status":"pass","reason":"ok"}'))
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    await c.evaluate_result(["frame1", "frame2", "frame3"], "All menus visible")
    body = fake.post.call_args.kwargs["json"]
    content = body["messages"][1]["content"]
    images = [b for b in content if b.get("type") == "image_url"]
    assert len(images) == 3
    assert images[0]["image_url"]["url"].endswith("frame1")
    assert images[2]["image_url"]["url"].endswith("frame3")


@pytest.mark.asyncio
async def test_evaluate_result_still_accepts_single_string():
    """Back-compat: a bare base64 string is treated as one frame."""
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(return_value=_ok('{"status":"pass","reason":"ok"}'))
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    await c.evaluate_result("only-frame", "Page loads")
    body = fake.post.call_args.kwargs["json"]
    images = [b for b in body["messages"][1]["content"] if b.get("type") == "image_url"]
    assert len(images) == 1


def test_parse_evaluation_accepts_blocked_with_findings():
    out = _parse_evaluation(
        '{"status":"blocked","reason":"Setting was never toggled",'
        '"findings":"Sidebar shows Equipment and Sites; no Account Maintenance visit occurred."}'
    )
    assert out["status"] == "blocked"
    assert "Setting was never toggled" in out["reason"]
    assert "Findings:" in out["reason"]
    assert "Account Maintenance" in out["reason"]


def test_parse_evaluation_findings_optional():
    out = _parse_evaluation('{"status":"pass","reason":"ok"}')
    assert out == {"status": "pass", "reason": "ok"}


def test_parse_actions_done_flag():
    actions, done = _parse_actions('{"actions": [], "done": true}')
    assert actions == [] and done is True
    actions, done = _parse_actions('{"actions": [{"action":"click","selector":"#x","value":null}]}')
    assert len(actions) == 1 and done is False


@pytest.mark.asyncio
async def test_translate_step_returns_empty_when_done():
    client, _fake = _client_with([_ok('{"actions": [], "done": true}')])
    actions = await client.translate_step("Verify done state")
    assert actions == []


@pytest.mark.asyncio
async def test_translate_step_still_errors_on_empty_without_done():
    client, _fake = _client_with([_ok('{"actions": []}')])
    with pytest.raises(AzureAIError):
        await client.translate_step("Do something")


def test_parse_actions_accepts_logout():
    """Verify that logout action is in the whitelist."""
    actions, done = _parse_actions('{"actions": [{"action": "logout"}]}')
    assert done is False
    assert actions == [
        {"action": "logout", "ref": None, "selector": None, "value": None}
    ]


# --- PERFORMED ACTIONS reach the evaluator -----------------------------------


@pytest.mark.asyncio
async def test_evaluate_result_includes_performed_actions_block():
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(return_value=_ok('{"status":"pass","reason":"ok"}'))
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    await c.evaluate_result("frame", "Expected X", performed="click; click")
    body = fake.post.call_args.kwargs["json"]
    content = body["messages"][1]["content"]
    text = "\n".join(b["text"] for b in content if b.get("type") == "text")
    assert "PERFORMED ACTIONS" in text
    assert "click; click" in text


@pytest.mark.asyncio
async def test_evaluate_result_omits_performed_block_when_not_given():
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(return_value=_ok('{"status":"pass","reason":"ok"}'))
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    await c.evaluate_result("frame", "Expected X")
    body = fake.post.call_args.kwargs["json"]
    content = body["messages"][1]["content"]
    text = "\n".join(b["text"] for b in content if b.get("type") == "text")
    assert "PERFORMED ACTIONS" not in text


# --- TESTER GUIDANCE reaches the evaluator -----------------------------------


@pytest.mark.asyncio
async def test_evaluate_result_includes_guidance_block():
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(return_value=_ok('{"status":"pass","reason":"ok"}'))
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    await c.evaluate_result(
        "frame", "Expected X", guidance="- tester overrode the AI's 'fail' to 'pass': known flaky banner"
    )
    body = fake.post.call_args.kwargs["json"]
    content = body["messages"][1]["content"]
    text = "\n".join(b["text"] for b in content if b.get("type") == "text")
    assert "TESTER GUIDANCE" in text
    assert "known flaky banner" in text


@pytest.mark.asyncio
async def test_evaluate_result_omits_guidance_block_when_not_given():
    fake = MagicMock(spec=httpx.AsyncClient)
    fake.post = AsyncMock(return_value=_ok('{"status":"pass","reason":"ok"}'))
    c = AzureAIClient(endpoint=ENDPOINT, api_key=KEY, deployment=DEPLOY, http_client=fake)
    await c.evaluate_result("frame", "Expected X")
    body = fake.post.call_args.kwargs["json"]
    content = body["messages"][1]["content"]
    text = "\n".join(b["text"] for b in content if b.get("type") == "text")
    assert "TESTER GUIDANCE" not in text
