"""Playwright async execution engine.

Supported actions (the only shapes the translator may emit):
  navigate         goto a relative or absolute URL
  click            click an element
  fill             type into an input
  select           pick an <option> by value (or visible label)
  wait             wait for an element to be visible
  assert_text      element exists AND its text contains `value`
  assert_visible   element exists AND is visible
  login            sign in as the configured test user (credentials from .env,
                   executed server-side — the model never sees them)
  logout           clear the session cookies and land back on the login page
                   (harness-executed; used when a step expects a logged-out state)

Always call `close_session()` in a finally block — Playwright's browser
process is heavy and lingers if you forget.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any
from urllib.parse import urljoin

from agent.url_banner import stamp_url

log = logging.getLogger(__name__)

# Default per-action timeout. Playwright will retry locator queries within this window.
DEFAULT_ACTION_TIMEOUT_MS = 15_000

MAX_SNAPSHOT_ELEMENTS = 60

# Hard caps for snapshot_table_data() — applied in Python regardless of what
# the page/JS produces, so a pathological page (huge grid, long cell text)
# can never bloat a translator prompt or a log line.
MAX_TABLE_ROWS = 15
MAX_TABLE_COLS = 6
MAX_TABLE_CELL_CHARS = 40

# Collect visible interactive elements, tag each with data-agent-ref="eN",
# and return [{ref, tag, role, name}]. Capped at MAX_SNAPSHOT_ELEMENTS.
_SNAPSHOT_JS = """
(maxN) => {
  const sels = ['button','a[href]','input','textarea','select',
    '[role=button]','[role=link]','[role=tab]','[role=menuitem]',
    '[role=checkbox]','[role=radio]'];
  const seen = new Set();
  const tagged = new Set();  // elements granted a ref in THIS snapshot
  const out = [];
  let n = 0;
  for (const sel of sels) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const r = el.getBoundingClientRect();
      const st = window.getComputedStyle(el);
      const visible = r.width > 0 && r.height > 0 &&
        st.visibility !== 'hidden' && st.display !== 'none';
      if (!visible) continue;
      n += 1;
      const ref = 'e' + n;
      el.setAttribute('data-agent-ref', ref);
      let name = (el.getAttribute('aria-label') || el.getAttribute('title') ||
        el.innerText || el.getAttribute('placeholder') || el.value || '').trim();
      if (!name) {
        // Icon-only control (legacy UI pencils/trashcans): derive a name from
        // the icon's class token, e.g. "fa fa-pencil" -> "pencil".
        const iconSel = '[class*="fa-"],[class*="icon-"],[class*="glyphicon-"]';
        const icon = el.matches(iconSel) ? el : el.querySelector(iconSel);
        const cls = icon ? (icon.getAttribute('class') || '') : '';
        const m = cls.match(/(?:fa|icon|glyphicon)-([a-z][a-z-]*)/);
        if (m) name = m[1];
      }
      if (!name && el.labels && el.labels.length) {
        // Unlabelled form field: use its <label> text, covering both
        // label[for=] and wrapping labels.
        name = (el.labels[0].innerText || '').trim();
      }
      if (el.type === 'checkbox' || el.type === 'radio') {
        // A checkbox's value ("1") is meaningless — its label is its name,
        // and the model needs the current state to reconcile/toggle.
        const lbl = (el.labels && el.labels[0]) || el.closest('label');
        const ltext = lbl ? (lbl.innerText || '').trim() : '';
        name = (ltext || el.id || name || el.type).slice(0, 60) +
          (el.checked ? ' (checked)' : ' (unchecked)');
      }
      // Controls inside a table row are ambiguous ("pencil" x10, or a bare
      // checkbox) — anchor them to the row's first cell.
      const row = el.closest('tr');
      if (row && name.length <= 14) {
        const cell = row.querySelector('td,th');
        const label = cell ? cell.innerText.trim().slice(0, 40) : '';
        if (label && !name.includes(label)) name = name ? name + ' — ' + label : label;
      }
      out.push({ref: ref, tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '', name: name.slice(0, 80)});
      tagged.add(el);
      if (out.length >= maxN) return out;
    }
  }
  // Legacy forms hide the real checkbox (0x0 input inside a styled span) and
  // leave only its LABEL visible. Expose those through the label: clicking
  // the label toggles the box. The name carries the current state so the
  // model can reconcile ("Asset Management (checked)").
  for (const cb of document.querySelectorAll('input[type=checkbox],input[type=radio]')) {
    if (tagged.has(cb)) continue;
    const lbl = (cb.labels && cb.labels[0]) || cb.closest('label');
    if (!lbl || tagged.has(lbl)) continue;
    const lr = lbl.getBoundingClientRect();
    const lst = window.getComputedStyle(lbl);
    if (!(lr.width > 0 && lr.height > 0 &&
          lst.visibility !== 'hidden' && lst.display !== 'none')) continue;
    n += 1;
    const ref = 'e' + n;
    lbl.setAttribute('data-agent-ref', ref);
    tagged.add(lbl);
    const label = ((lbl.innerText || '').trim() || cb.id || 'checkbox').slice(0, 60);
    out.push({ref: ref, tag: 'input', role: cb.type,
              name: label + (cb.checked ? ' (checked)' : ' (unchecked)')});
    if (out.length >= maxN) return out;
  }
  return out;
}
"""


# Collect the first visible <table>'s header + body cell text, uncapped —
# Python applies MAX_TABLE_ROWS/COLS/CELL_CHARS afterwards so the cap holds
# no matter what a page's markup looks like.
_TABLE_SNAPSHOT_JS = """
() => {
  function text(el) { return (el.innerText || '').replace(/\\s+/g, ' ').trim(); }
  let pendingHeaders = [];
  for (const table of document.querySelectorAll('table')) {
    const r = table.getBoundingClientRect();
    const st = window.getComputedStyle(table);
    const visible = r.width > 0 && r.height > 0 &&
      st.visibility !== 'hidden' && st.display !== 'none';
    if (!visible) continue;
    let headerCells = table.querySelectorAll('thead th, thead td');
    if (!headerCells.length) {
      const firstRow = table.querySelector('tr');
      headerCells = (firstRow && firstRow.querySelector('th'))
        ? firstRow.querySelectorAll('th') : [];
    }
    const headers = Array.from(headerCells).map(text);
    let bodyRows = Array.from(table.querySelectorAll('tbody tr'));
    if (!bodyRows.length) {
      bodyRows = Array.from(table.querySelectorAll('tr')).filter(tr => !tr.querySelector('th'));
    }
    const rows = bodyRows.map(tr => Array.from(tr.querySelectorAll('td')).map(text));
    // Prefer a table that actually has DATA. DataTables (used throughout this
    // app) splits one logical grid into two <table> elements: a header-only
    // table for the fixed header, and a second table holding the body rows.
    // Returning on the first table with *either* headers or rows picked the
    // header-only one and reported zero rows, which made the whole snapshot
    // useless on every list page. Remember the first headers we see, keep
    // looking for rows, and pair them up.
    if (rows.length) {
      return {headers: headers.length ? headers : pendingHeaders, rows: rows};
    }
    if (headers.length && !pendingHeaders.length) pendingHeaders = headers;
  }
  // No table had body rows — hand back any headers we found, so the caller can
  // still see the shape of the page.
  return {headers: pendingHeaders, rows: []};
}
"""


class BrowserError(Exception):
    """Raised when an action fails. Carries a short, human-readable reason."""


class BrowserSession:
    """One agent run owns one BrowserSession.

    Tests can inject `page` / `context` / `browser` to avoid launching Chromium.
    """

    def __init__(
        self,
        headless: bool | None = None,
        base_url: str | None = None,
        action_timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS,
    ):
        if headless is None:
            headless = os.environ.get("HEADLESS", "true").lower() == "true"
        self.headless = headless
        self.base_url = (base_url or os.environ.get("APP_BASE_URL", "")).rstrip("/")
        self.action_timeout_ms = action_timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        # Optional per-run (username, password) override for login(); set by
        # the orchestrator for Manual-tab runs. None = use the .env account.
        self.credentials: tuple[str, str] | None = None

    # -------------------------------------------------------------- lifecycle

    async def open_session(self) -> None:
        """Launch Chromium and open a fresh context + page."""
        from playwright.async_api import async_playwright  # local import: heavy

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.action_timeout_ms)

    async def close_session(self) -> None:
        """Tear down everything. Safe to call even if open_session failed midway."""
        try:
            if self._context is not None:
                await self._context.close()
        finally:
            self._context = None
            try:
                if self._browser is not None:
                    await self._browser.close()
            finally:
                self._browser = None
                if self._playwright is not None:
                    await self._playwright.stop()
                    self._playwright = None
                self._page = None

    # -------------------------------------------------------------- primitives

    async def screenshot(self) -> str:
        """Return a base64 PNG of the current page, with a URL strip on top.

        The banner is drawn here — the single chokepoint every capture passes
        through — so per-action evaluator frames and the final stored frame all
        carry the URL. Stamping never raises: a cosmetic banner must not fail a
        step, so a failure yields the unmodified screenshot.
        """
        if self._page is None:
            raise BrowserError("Cannot screenshot — no active page")
        png: bytes = await self._page.screenshot(type="png", full_page=False)
        try:
            png = stamp_url(png, await self.current_url())
        except Exception:
            log.debug("URL banner failed; using raw screenshot", exc_info=True)
        return base64.b64encode(png).decode("ascii")

    async def wait_for_settle(self, quiet_ms: int = 800, timeout_ms: int = 15_000) -> None:
        """Give the page time to finish in-flight work before a screenshot.

        Screenshots taken immediately after an action capture mid-transition UI
        (a nav submenu still sliding open, or a click whose server-side
        navigation takes several seconds), which makes the vision evaluator
        fail steps that actually succeeded. Waits for network idle up to
        timeout_ms — networkidle fires early on fast pages, and a pending
        navigation keeps the network busy so this also rides out slow
        server responses. Pages that never go idle just pay the timeout.
        Then a fixed pause so CSS transitions finish.
        """
        if self._page is None:
            return
        try:
            await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass
        await self._page.wait_for_timeout(quiet_ms)

    async def current_url(self) -> str:
        if self._page is None:
            return ""
        return self._page.url

    async def snapshot_elements(self) -> list[dict[str, Any]]:
        """Tag visible interactive elements with data-agent-ref and return them.

        Each entry: {ref, tag, role, name}. The ref is resolvable via the
        selector [data-agent-ref="<ref>"]. Returns [] if evaluation fails.
        """
        if self._page is None:
            raise BrowserError("No active page — call open_session() first")
        try:
            elements = await self._page.evaluate(_SNAPSHOT_JS, MAX_SNAPSHOT_ELEMENTS)
        except Exception as e:  # page closed, JS error, etc.
            log.warning("snapshot_elements failed: %s", e)
            return []
        if len(elements) >= MAX_SNAPSHOT_ELEMENTS:
            log.warning("Element snapshot truncated to %d", MAX_SNAPSHOT_ELEMENTS)
        return elements

    async def snapshot_table_data(self) -> dict[str, list]:
        """Return the first visible on-page table as compact structured data.

        `{"headers": [...], "rows": [[...], ...]}` — used to give the
        translator real tabular values (e.g. a Users table's email column)
        for steps that need a value which must already exist in the app,
        rather than let the model invent one. Hard-capped at
        MAX_TABLE_ROWS rows, MAX_TABLE_COLS cells per row, and
        MAX_TABLE_CELL_CHARS characters per cell (longer cells are
        truncated with an ellipsis) — enforced here in Python regardless of
        what the page/JS produces, so this can never bloat a prompt or a log
        line. Empty cells are dropped. Returns `{"headers": [], "rows": []}`
        if there is no table on the page or evaluation fails — never raises
        (except the same "no active page" guard as `snapshot_elements`).
        """
        if self._page is None:
            raise BrowserError("No active page — call open_session() first")
        try:
            raw = await self._page.evaluate(_TABLE_SNAPSHOT_JS)
        except Exception as e:  # page closed, JS error, etc.
            log.warning("snapshot_table_data failed: %s", e)
            return {"headers": [], "rows": []}
        if not isinstance(raw, dict):
            return {"headers": [], "rows": []}

        def _cell(v: Any) -> str:
            s = str(v or "").strip()
            if len(s) > MAX_TABLE_CELL_CHARS:
                s = s[: MAX_TABLE_CELL_CHARS - 1] + "…"
            return s

        raw_headers = raw.get("headers") or []
        headers = [c for c in (_cell(h) for h in raw_headers[:MAX_TABLE_COLS]) if c]

        raw_rows = raw.get("rows") or []
        rows: list[list[str]] = []
        for row in raw_rows[:MAX_TABLE_ROWS]:
            cells = [c for c in (_cell(v) for v in (row or [])[:MAX_TABLE_COLS]) if c]
            if cells:
                rows.append(cells)

        if len(raw_rows) > MAX_TABLE_ROWS:
            log.warning("Table snapshot truncated to %d rows", MAX_TABLE_ROWS)

        return {"headers": headers, "rows": rows}

    # -------------------------------------------------------------- dispatcher

    async def execute_action(self, action: dict[str, Any]) -> None:
        """Run one action. Raises BrowserError on failure with a short reason."""
        if self._page is None:
            raise BrowserError("No active page — call open_session() first")

        name = action.get("action")
        ref = action.get("ref")
        # A ref points at an element tagged by snapshot_elements(); resolve it to
        # the data-attribute selector. Falls back to an explicit selector.
        selector = f'[data-agent-ref="{ref}"]' if ref else action.get("selector")
        value = action.get("value")

        handler = _DISPATCH.get(name)
        if handler is None:
            raise BrowserError(f"Unknown action {name!r}")
        try:
            await handler(self, selector, value)
        except BrowserError:
            raise
        except Exception as e:
            # Wrap any Playwright exception so callers can render a clean reason.
            raise BrowserError(
                f"{name} failed on {selector!r}: {type(e).__name__}: {e}"
            ) from e

    # -------------------------------------------------------------- handlers

    async def _navigate(self, selector: str | None, value: str | None) -> None:
        target = selector or value
        if not target:
            raise BrowserError("navigate needs a URL in 'selector' or 'value'")
        url = target if "://" in target else urljoin(self.base_url + "/", target.lstrip("/"))
        # "commit" (not "domcontentloaded"): the app's pages are heavy legacy jQuery
        # pages whose DOMContentLoaded can stall for >60s. The subsequent action
        # (click/fill/wait) auto-waits for its target, so commit is enough.
        await self._page.goto(url, wait_until="commit")

    async def _click(self, selector: str | None, value: str | None) -> None:
        if not selector:
            raise BrowserError("click needs a selector")
        await self._page.click(selector)

    async def _fill(self, selector: str | None, value: str | None) -> None:
        if not selector:
            raise BrowserError("fill needs a selector")
        await self._page.fill(selector, value or "")

    async def _select(self, selector: str | None, value: str | None) -> None:
        if not selector:
            raise BrowserError("select needs a selector")
        # accepts option value OR visible label
        await self._page.select_option(selector, value)

    async def _wait(self, selector: str | None, value: str | None) -> None:
        if not selector:
            raise BrowserError("wait needs a selector")
        await self._page.wait_for_selector(selector, state="visible")

    async def _assert_text(self, selector: str | None, value: str | None) -> None:
        if not selector:
            raise BrowserError("assert_text needs a selector")
        await self._page.wait_for_selector(selector, state="visible")
        actual = (await self._page.text_content(selector)) or ""
        if value and value not in actual:
            raise BrowserError(
                f"assert_text: expected {value!r} in element text, got {actual!r}"
            )

    async def _assert_visible(self, selector: str | None, value: str | None) -> None:
        if not selector:
            raise BrowserError("assert_visible needs a selector")
        await self._page.wait_for_selector(selector, state="visible")

    async def _login(self, selector: str | None, value: str | None) -> None:
        """Sign in as the configured test user; credentials never reach the model."""
        from agent.login import login as perform_login  # local import — login.py imports us

        await perform_login(self)

    async def _logout(self, selector: str | None, value: str | None) -> None:
        """Kill the session and land on the login page.

        Deterministic from any page state: clearing cookies beats clicking the
        UI Logout entry, which lives in a collapsible sidebar and can be
        blocked by the cookie banner. The model never sees session mechanics.
        """
        from agent.login import _LOGIN_TIMEOUT_MS, _dismiss_cookie_banner  # local: login.py imports us

        if self._context is not None:
            await self._context.clear_cookies()
        await self._page.goto(self.base_url or "/", wait_until="commit")
        try:
            await self._page.wait_for_selector(
                'input[placeholder="Email address"]',
                state="visible",
                timeout=_LOGIN_TIMEOUT_MS,
            )
        except Exception:
            raise BrowserError(
                "logout: cookies cleared but the login page never appeared — "
                f"check APP_BASE_URL ({self.base_url!r}) points at the login form."
            ) from None
        await _dismiss_cookie_banner(self._page)


_DISPATCH = {
    "navigate":       BrowserSession._navigate,
    "click":          BrowserSession._click,
    "fill":           BrowserSession._fill,
    "select":         BrowserSession._select,
    "wait":           BrowserSession._wait,
    "assert_text":    BrowserSession._assert_text,
    "assert_visible": BrowserSession._assert_visible,
    "login":          BrowserSession._login,
    "logout":         BrowserSession._logout,
}
