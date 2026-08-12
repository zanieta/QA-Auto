"""BrowserSession tests â€” Playwright Page is mocked, no real chromium.

Verifies each action dispatches to the right Playwright call with the right
args, relative URLs resolve against APP_BASE_URL, and assert_text correctly
distinguishes match vs. mismatch.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.browser import BrowserError, BrowserSession


def _session_with_fake_page(base_url: str = "https://app.example.com") -> tuple[BrowserSession, MagicMock]:
    s = BrowserSession(headless=True, base_url=base_url)
    page = MagicMock()
    page.goto = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.select_option = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.text_content = AsyncMock(return_value="")
    page.screenshot = AsyncMock(return_value=b"\x89PNG-bytes")
    page.url = base_url
    s._page = page
    return s, page


@pytest.mark.asyncio
async def test_navigate_resolves_relative_url_against_base():
    s, page = _session_with_fake_page("https://app.example.com")
    await s.execute_action({"action": "navigate", "selector": "/inventory/recipes", "value": None})
    page.goto.assert_awaited_once_with(
        "https://app.example.com/inventory/recipes", wait_until="commit"
    )


@pytest.mark.asyncio
async def test_navigate_passes_absolute_url_through():
    s, page = _session_with_fake_page()
    await s.execute_action({"action": "navigate", "selector": "https://other.example.com/x", "value": None})
    page.goto.assert_awaited_once_with(
        "https://other.example.com/x", wait_until="commit"
    )


@pytest.mark.asyncio
async def test_click_dispatches_to_page_click():
    s, page = _session_with_fake_page()
    await s.execute_action({"action": "click", "selector": "[data-test=save]", "value": None})
    page.click.assert_awaited_once_with("[data-test=save]")


@pytest.mark.asyncio
async def test_fill_passes_value():
    s, page = _session_with_fake_page()
    await s.execute_action({"action": "fill", "selector": "#name", "value": "Grilled Salmon"})
    page.fill.assert_awaited_once_with("#name", "Grilled Salmon")


@pytest.mark.asyncio
async def test_select_passes_value():
    s, page = _session_with_fake_page()
    await s.execute_action({"action": "select", "selector": "#country", "value": "US"})
    page.select_option.assert_awaited_once_with("#country", "US")


@pytest.mark.asyncio
async def test_wait_calls_wait_for_selector_visible():
    s, page = _session_with_fake_page()
    await s.execute_action({"action": "wait", "selector": ".loaded", "value": None})
    page.wait_for_selector.assert_awaited_once_with(".loaded", state="visible")


@pytest.mark.asyncio
async def test_assert_text_match_passes():
    s, page = _session_with_fake_page()
    page.text_content.return_value = "Hello, world"
    await s.execute_action({"action": "assert_text", "selector": "h1", "value": "Hello"})


@pytest.mark.asyncio
async def test_assert_text_mismatch_raises_browser_error():
    s, page = _session_with_fake_page()
    page.text_content.return_value = "Goodbye"
    with pytest.raises(BrowserError, match="assert_text"):
        await s.execute_action({"action": "assert_text", "selector": "h1", "value": "Hello"})


@pytest.mark.asyncio
async def test_assert_visible_waits_then_returns():
    s, page = _session_with_fake_page()
    await s.execute_action({"action": "assert_visible", "selector": ".banner", "value": None})
    page.wait_for_selector.assert_awaited_once_with(".banner", state="visible")


@pytest.mark.asyncio
async def test_unknown_action_raises():
    s, _ = _session_with_fake_page()
    with pytest.raises(BrowserError, match="Unknown action"):
        await s.execute_action({"action": "explode", "selector": "x", "value": None})


@pytest.mark.asyncio
async def test_missing_selector_raises():
    s, _ = _session_with_fake_page()
    with pytest.raises(BrowserError):
        await s.execute_action({"action": "click", "selector": None, "value": None})


@pytest.mark.asyncio
async def test_playwright_exception_is_wrapped_as_browser_error():
    s, page = _session_with_fake_page()
    page.click.side_effect = RuntimeError("element detached")
    with pytest.raises(BrowserError, match="click failed"):
        await s.execute_action({"action": "click", "selector": "#x", "value": None})


@pytest.mark.asyncio
async def test_screenshot_returns_base64_png():
    s, page = _session_with_fake_page()
    page.screenshot.return_value = b"hello"
    b64 = await s.screenshot()
    # base64('hello') == 'aGVsbG8='
    assert b64 == "aGVsbG8="
    page.screenshot.assert_awaited_once_with(type="png", full_page=False)


@pytest.mark.asyncio
async def test_execute_without_open_session_raises():
    s = BrowserSession(base_url="https://x")
    with pytest.raises(BrowserError, match="No active page"):
        await s.execute_action({"action": "click", "selector": "#x", "value": None})


# --- DOM snapshot + ref resolution -------------------------------------------
import agent.browser as browser_mod


@pytest.mark.asyncio
async def test_snapshot_elements_returns_page_evaluate_result():
    s, page = _session_with_fake_page("https://app.example.com")
    page.evaluate = AsyncMock(
        return_value=[
            {"ref": "e1", "tag": "input", "role": "", "name": "Email address"},
            {"ref": "e2", "tag": "a", "role": "link", "name": "Logout"},
        ]
    )
    out = await s.snapshot_elements()
    assert out[0]["ref"] == "e1"
    assert out[1]["name"] == "Logout"
    page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_elements_empty_on_evaluate_error():
    s, page = _session_with_fake_page()
    page.evaluate = AsyncMock(side_effect=RuntimeError("evaluate boom"))
    out = await s.snapshot_elements()
    assert out == []


@pytest.mark.asyncio
async def test_execute_action_resolves_ref_to_data_attr_selector():
    s, page = _session_with_fake_page()
    await s.execute_action({"action": "click", "ref": "e2", "value": None})
    page.click.assert_awaited_once_with('[data-agent-ref="e2"]')


@pytest.mark.asyncio
async def test_execute_action_ref_fill_uses_value():
    s, page = _session_with_fake_page()
    await s.execute_action({"action": "fill", "ref": "e1", "value": "joe@x.com"})
    page.fill.assert_awaited_once_with('[data-agent-ref="e1"]', "joe@x.com")


@pytest.mark.asyncio
async def test_login_action_signs_in_with_server_side_credentials():
    """The 'login' action delegates to agent.login.login — the model never
    sees credentials."""
    from unittest.mock import patch

    s, _page = _session_with_fake_page()
    with patch("agent.login.login", new=AsyncMock()) as fake_login:
        await s.execute_action({"action": "login", "selector": None, "value": None})
    fake_login.assert_awaited_once_with(s)


@pytest.mark.asyncio
async def test_logout_action_clears_cookies_and_lands_on_login_page():
    """'logout' kills the session cookie and waits for the login form —
    deterministic no matter what page or UI state the agent is in."""
    s, page = _session_with_fake_page("https://app.example.com")
    context = MagicMock()
    context.clear_cookies = AsyncMock()
    s._context = context
    # locator used by the cookie-banner dismissal; its awaits may fail freely
    page.locator = MagicMock()

    await s.execute_action({"action": "logout", "selector": None, "value": None})

    context.clear_cookies.assert_awaited_once_with()
    page.goto.assert_awaited_once_with("https://app.example.com", wait_until="commit")
    args, kwargs = page.wait_for_selector.await_args
    assert args[0] == 'input[placeholder="Email address"]'
    assert kwargs.get("state") == "visible"


@pytest.mark.asyncio
async def test_logout_raises_browser_error_when_login_page_never_appears():
    s, page = _session_with_fake_page()
    s._context = MagicMock()
    s._context.clear_cookies = AsyncMock()
    page.locator = MagicMock()
    page.wait_for_selector = AsyncMock(side_effect=TimeoutError("email field"))

    with pytest.raises(BrowserError):
        await s.execute_action({"action": "logout", "selector": None, "value": None})


# --- per-case credentials override -------------------------------------------


def test_browser_session_credentials_defaults_to_none():
    s = BrowserSession(base_url="https://x")
    assert s.credentials is None


@pytest.mark.asyncio
async def test_login_uses_session_credentials_over_env(monkeypatch):
    from agent.login import login

    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("APP_USERNAME", "env-user@x.com")
    monkeypatch.setenv("APP_PASSWORD", "env-pw")
    s, page = _session_with_fake_page("https://app.example.com")
    page.locator = MagicMock()
    page.wait_for_url = AsyncMock()
    s.credentials = ("case-user@x.com", "case-pw")

    await login(s)

    fill_args = [c.args for c in page.fill.await_args_list]
    assert ('input[placeholder="Email address"]', "case-user@x.com") in fill_args
    assert ('input[type="password"]', "case-pw") in fill_args


@pytest.mark.asyncio
async def test_login_falls_back_to_env_without_session_credentials(monkeypatch):
    from agent.login import login

    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("APP_USERNAME", "env-user@x.com")
    monkeypatch.setenv("APP_PASSWORD", "env-pw")
    s, page = _session_with_fake_page("https://app.example.com")
    page.locator = MagicMock()
    page.wait_for_url = AsyncMock()

    await login(s)

    fill_args = [c.args for c in page.fill.await_args_list]
    assert ('input[placeholder="Email address"]', "env-user@x.com") in fill_args


@pytest.mark.asyncio
async def test_screenshot_stamps_the_current_url(monkeypatch):
    seen = {}

    def fake_stamp(png: bytes, url: str) -> bytes:
        seen["png"] = png
        seen["url"] = url
        return b"STAMPED"

    monkeypatch.setattr("agent.browser.stamp_url", fake_stamp)
    s, page = _session_with_fake_page("https://app.example.com")
    page.url = "https://app.example.com/account/recipes"
    page.screenshot = AsyncMock(return_value=b"RAW")

    result = await s.screenshot()

    assert seen["png"] == b"RAW"
    assert seen["url"] == "https://app.example.com/account/recipes"
    assert base64.b64decode(result) == b"STAMPED"


@pytest.mark.asyncio
async def test_screenshot_returns_raw_bytes_when_stamping_fails(monkeypatch):
    def boom(png: bytes, url: str) -> bytes:
        raise RuntimeError("no pillow")

    monkeypatch.setattr("agent.browser.stamp_url", boom)
    s, page = _session_with_fake_page()
    page.screenshot = AsyncMock(return_value=b"RAW")

    assert base64.b64decode(await s.screenshot()) == b"RAW"
