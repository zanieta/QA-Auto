"""Tests for agent/login.py — BrowserSession page is mocked, no real Chromium."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.browser import BrowserError, BrowserSession
from agent.login import login


def _session(base_url: str = "https://test.souscheftech.com/login") -> tuple[BrowserSession, MagicMock]:
    s = BrowserSession(headless=True, base_url=base_url)
    page = MagicMock()
    page.goto = AsyncMock()
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.url = base_url

    # the login form appears (login() waits for the email field after commit)
    page.wait_for_selector = AsyncMock()
    # wait_for_url succeeds by default (navigated away from /login)
    page.wait_for_url = AsyncMock()

    # locator() for error-message detection
    loc = MagicMock()
    loc.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=loc)

    s._page = page
    return s, page


@pytest.mark.asyncio
async def test_login_happy_path(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://test.souscheftech.com/login")
    monkeypatch.setenv("APP_USERNAME", "user@example.com")
    monkeypatch.setenv("APP_PASSWORD", "secret")

    session, page = _session()
    await login(session)

    page.goto.assert_awaited_once()
    # email and password filled in order
    calls = [c.args for c in page.fill.await_args_list]
    assert calls[0][1] == "user@example.com"
    assert calls[1][1] == "secret"
    page.click.assert_awaited_once()
    page.wait_for_url.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_missing_credentials_raises(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://test.souscheftech.com/login")
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    session, _ = _session()
    with pytest.raises(BrowserError, match="APP_USERNAME"):
        await login(session)


@pytest.mark.asyncio
async def test_login_before_open_session_raises(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://test.souscheftech.com/login")
    monkeypatch.setenv("APP_USERNAME", "u")
    monkeypatch.setenv("APP_PASSWORD", "p")

    session = BrowserSession()  # _page is None
    with pytest.raises(BrowserError, match="open_session"):
        await login(session)


@pytest.mark.asyncio
async def test_login_shows_inline_error(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://test.souscheftech.com/login")
    monkeypatch.setenv("APP_USERNAME", "bad@example.com")
    monkeypatch.setenv("APP_PASSWORD", "wrong")

    session, page = _session()

    # wait_for_url raises (still on /login)
    page.wait_for_url = AsyncMock(side_effect=Exception("timeout"))

    # error locator finds a message
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    loc.first = MagicMock()
    loc.first.text_content = AsyncMock(return_value="Invalid email or password")
    page.locator = MagicMock(return_value=loc)

    with pytest.raises(BrowserError, match="Login rejected"):
        await login(session)


@pytest.mark.asyncio
async def test_login_timeout_no_error_element(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://test.souscheftech.com/login")
    monkeypatch.setenv("APP_USERNAME", "u")
    monkeypatch.setenv("APP_PASSWORD", "p")

    session, page = _session()
    page.wait_for_url = AsyncMock(side_effect=Exception("timeout"))

    loc = MagicMock()
    loc.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=loc)

    with pytest.raises(BrowserError, match="timed out"):
        await login(session)
