"""Log in to Sous Chef Cloud before running test steps.

Called by the orchestrator once after open_session(), before any test actions.
The app uses a plain email/password form — no SSO redirect.
"""

from __future__ import annotations

import logging
import os

from agent.browser import BrowserError, BrowserSession

log = logging.getLogger(__name__)

# Generous timeout: the FIRST navigation after a cold browser launch can take
# 15-25s on this network, while a warm load is ~6s. 20s intermittently timed out.
_LOGIN_TIMEOUT_MS = 60_000


async def _dismiss_cookie_banner(page) -> None:
    """Click the cookie-consent Accept button if the banner is showing.

    The banner renders late (async script) and can reappear after the
    post-login navigation, so this is called both before and after login.
    Non-fatal if absent.
    """
    try:
        accept_btn = page.locator('button:has-text("Accept")')
        await accept_btn.first.wait_for(state="visible", timeout=3_000)
        await accept_btn.first.click(timeout=3_000)
        log.debug("Cookie banner dismissed")
    except Exception:
        pass


async def login(browser: BrowserSession) -> None:
    """Fill credentials on the login page and wait to land on the dashboard.

    Raises BrowserError if credentials are missing, the form rejects them, or
    navigation times out.
    """
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    override = getattr(browser, "credentials", None)
    if override:
        username, password = override
    else:
        username = os.environ.get("APP_USERNAME", "")
        password = os.environ.get("APP_PASSWORD", "")

    if not username or not password:
        raise BrowserError(
            "No login credentials: set APP_USERNAME/APP_PASSWORD in .env or "
            "per-case credentials"
        )

    page = browser._page
    if page is None:
        raise BrowserError("login() called before open_session()")

    log.info("Navigating to login page: %s", base_url)
    # NOTE: wait_until="commit", not "domcontentloaded". The Sous Chef login page
    # is a heavy legacy jQuery page whose DOMContentLoaded event can take >60s to
    # fire (a synchronous head script stalls it) even though the page and all its
    # assets load fine. "commit" returns as soon as the navigation commits; we then
    # wait for the email field specifically — the only readiness signal we need.
    await page.goto(base_url, wait_until="commit", timeout=_LOGIN_TIMEOUT_MS)

    # Wait for the actual login form rather than a document-level load event.
    try:
        await page.wait_for_selector(
            'input[placeholder="Email address"]', state="visible", timeout=_LOGIN_TIMEOUT_MS
        )
    except Exception:
        raise BrowserError(
            "Login page loaded but the email field never appeared — "
            f"check APP_BASE_URL ({base_url}) points at the login form."
        ) from None

    await _dismiss_cookie_banner(page)

    await page.fill('input[placeholder="Email address"]', username)
    await page.fill('input[type="password"]', password)
    await page.click('button:has-text("Sign In")')

    # Wait to navigate away from /login
    try:
        await page.wait_for_url(
            lambda url: "/login" not in url,
            timeout=_LOGIN_TIMEOUT_MS,
        )
    except Exception:
        # Try to surface an inline error message before raising a generic timeout.
        error_loc = page.locator('[class*="error"], [class*="alert"], [role="alert"]')
        if await error_loc.count() > 0:
            msg = (await error_loc.first.text_content() or "").strip()
            raise BrowserError(f"Login rejected: {msg!r}") from None
        raise BrowserError(
            "Login timed out — still on /login after 20 s. "
            "Check APP_USERNAME / APP_PASSWORD in .env."
        ) from None

    # The banner can re-render on the post-login page and would block clicks
    # near the bottom of the viewport, so try once more now.
    await _dismiss_cookie_banner(page)

    log.info("Login OK — landed at %s", page.url)
