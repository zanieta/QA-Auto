# Logout Action + Precondition Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a harness-executed `logout` browser action and a RECONCILE prompt rule so the agent can satisfy steps that expect a logged-out state (spec: `docs/superpowers/specs/2026-07-07-precondition-reconciliation-design.md`).

**Architecture:** Three touchpoints, no orchestrator change: (1) `agent/browser.py` gains a `_logout` handler mirroring `_login` (cookie clear + goto login page + wait for email field + dismiss banner); (2) `agent/azure_ai.py` `VALID_ACTIONS` must include `"logout"` or the parser raises at translate time; (3) `prompts/step_translator.txt` gains the action and the RECONCILE rule. The act→observe loop already re-snapshots after the logout navigation.

**Tech Stack:** Python 3.14 (`.venv\Scripts\python.exe`), pytest + AsyncMock (no real Chromium/network in tests), Playwright async API.

## Global Constraints

- Always run Python via `.venv\Scripts\python.exe` (Windows venv at repo root).
- Tests never launch Chromium or hit the network — mock the Playwright Page/context.
- The model never sees credentials or session mechanics; `logout` takes no ref/value.
- This directory is NOT a git repository — skip commit steps; the "commit" of each task is a green targeted test run.
- Login-page readiness selector everywhere: `input[placeholder="Email address"]`.

---

### Task 1: `logout` action in `agent/browser.py`

**Files:**
- Modify: `agent/browser.py` (docstring action list ~line 3-13, handlers after `_login` ~line 253, `_DISPATCH` ~line 255)
- Test: `tests/test_browser.py` (append after `test_login_action_signs_in_with_server_side_credentials`, ~line 189)

**Interfaces:**
- Consumes: `agent.login._dismiss_cookie_banner(page)` and `agent.login._LOGIN_TIMEOUT_MS` (both exist in `agent/login.py`).
- Produces: `BrowserSession._logout(selector, value)` reachable via `execute_action({"action": "logout"})`. Task 2's parser and Task 3's prompt rely on the action name `"logout"` exactly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_browser.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_browser.py -q -k logout`
Expected: 2 FAILED with `BrowserError: Unknown action 'logout'`

- [ ] **Step 3: Implement `_logout`**

In `agent/browser.py`, add after `_login` (line 253):

```python
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
```

Register it in `_DISPATCH` (line 255):

```python
    "logout":         BrowserSession._logout,
```

Add `logout` to the module docstring's action list (after the `login` line):

```
  logout           clear the session cookies and land back on the login page
                   (harness-executed; used when a step expects a logged-out state)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_browser.py -q`
Expected: all pass (existing + 2 new)

Note: the first test's banner dismissal goes through `_dismiss_cookie_banner`,
which swallows every exception — the MagicMock locator's non-awaitable methods
raise inside it and are swallowed by design. No banner-specific mocking needed.

---

### Task 2: `logout` in the translator whitelist (`agent/azure_ai.py`)

**Files:**
- Modify: `agent/azure_ai.py:28-37` (`VALID_ACTIONS`)
- Test: `tests/test_azure_ai.py` (append near the existing login-parse test if present, else at end)

**Interfaces:**
- Consumes: `_parse_actions` behavior — raises `AzureAIError` for any `action` not in `VALID_ACTIONS` (azure_ai.py:313).
- Produces: `"logout"` accepted by the parser; orchestrator receives `{"action": "logout", "ref": None, "selector": None, "value": None}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_azure_ai.py` (it already imports the module's parse
helper or exercises `translate_step`; follow the file's existing pattern —
if it tests via `_parse_actions`, use):

```python
def test_parse_actions_accepts_logout():
    from agent.azure_ai import _parse_actions

    actions, done = _parse_actions('{"actions": [{"action": "logout"}]}')
    assert done is False
    assert actions == [
        {"action": "logout", "ref": None, "selector": None, "value": None}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_azure_ai.py -q -k logout`
Expected: FAIL with `AzureAIError: Action 0 has unknown 'action': 'logout'`

- [ ] **Step 3: Add to whitelist**

In `agent/azure_ai.py`, `VALID_ACTIONS` (line 28):

```python
VALID_ACTIONS = {
    "navigate",
    "click",
    "fill",
    "select",
    "wait",
    "assert_text",
    "assert_visible",
    "login",
    "logout",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_azure_ai.py -q`
Expected: all pass

---

### Task 3: RECONCILE rule in `prompts/step_translator.txt`

**Files:**
- Modify: `prompts/step_translator.txt` (action list line 29; new rule adjacent to the login rule at lines 43-47)

**Interfaces:**
- Consumes: action names `login` / `logout` from Tasks 1-2.
- Produces: prompt text only — no code contract.

- [ ] **Step 1: Update the action list (line 29)**

```
  - "action": one of navigate | click | fill | select | wait | assert_text | assert_visible | login | logout
```

- [ ] **Step 2: Add the RECONCILE rule**

Insert as the FIRST entry under `Rules:` (before the "Every element in PAGE
ELEMENTS…" rule), so state reconciliation precedes element-targeting logic:

```
- RECONCILE FIRST: compare what the CURRENT step requires with what the page
  shows before planning any actions. If the step expects the login page or a
  logged-out state and PAGE ELEMENTS show you are logged in (welcome banner,
  app sidebar menus), emit {"action": "logout"} (no ref, no value) FIRST —
  the harness clears the session and lands on the login page. If the step
  assumes you are logged in but you see the login page, emit
  {"action": "login"} first. Then continue with the step's own actions.
```

- [ ] **Step 3: Sanity-check the suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass (prompt is data; nothing asserts its exact text)

---

### Task 4: Verification — suite + live acceptance

**Files:**
- No changes. Server must be running (`scripts\serve.cmd`).

**Interfaces:**
- Consumes: `POST /manual/{plan}/cases/{id}/run-agent` with `{"steps": [0]}`; `GET /runs/{id}`.

- [ ] **Step 1: Full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass (was 135+ before this change)

- [ ] **Step 2: Live acceptance (spec's acceptance criterion)**

Run TC-2 step 1 alone against cycle `daYoCqgmH49VMx` (the cycle where the
failure was observed):

```powershell
$b = '{"steps": [0]}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/manual/daYoCqgmH49VMx/cases/SOUSCLOUD-TC-2/run-agent" -Method Post -ContentType "application/json" -Body $b
```

Poll `GET /runs/{run_id}` until `status == "done"`.
Expected: the step's `detail` contains `logout`; step status `pass`; the
evaluation says the login page is visible.

- [ ] **Step 3: Regression — steps 1-3 together**

Same POST with `{"steps": [0, 1, 2]}`.
Expected: step 1 pass via logout; step 2 emits `login` and (on this cycle's
TC-2 text) resolves per its expected text — pass or blocked-on-
`[unresolved reference]` is acceptable; it must NOT fail because of auth state.
