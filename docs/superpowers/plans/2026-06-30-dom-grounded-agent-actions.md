# DOM-Grounded Agent Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the agent guessing CSS selectors blind — snapshot the page's real interactive elements, let GPT-4o pick one by a stable `ref`, resolve that ref to the actual element, and re-snapshot + retry once on failure.

**Architecture:** `browser.py` gains `snapshot_elements()` which tags each visible interactive element with a `data-agent-ref` attribute and returns `{ref, tag, role, name}`. `azure_ai.py` feeds that list into the translate prompt; the model returns `{action, ref, value}`. `execute_action` resolves `ref` → `[data-agent-ref="…"]`. The orchestrator snapshots before translating and, on a `BrowserError`, re-snapshots + re-translates + retries the step once before failing.

**Tech Stack:** Python 3.14 async, Playwright (mocked Page in tests), httpx, pytest.

## Global Constraints

- Python invoked only via `.venv\Scripts\python.exe` (Windows venv).
- **Repo is NOT under git.** Ignore "Commit" steps — each task ends with a **Checkpoint**: `.venv\Scripts\python.exe -m pytest tests/ -q` green.
- Tests mock the Playwright `Page` — never launch Chromium, never hit the network.
- Action vocabulary unchanged: navigate, click, fill, select, wait, assert_text, assert_visible.
- `MAX_SNAPSHOT_ELEMENTS = 60`.
- Element actions carry `ref`; `navigate` carries `value` (URL). `ref` resolves to the CSS selector `[data-agent-ref="{ref}"]`.
- Evaluation (screenshot → GPT-4o vision → pass/fail) is UNCHANGED.
- The translator runs in `response_format={"type":"json_object"}`, so the model returns an OBJECT `{"actions":[...]}` — never a bare array.
- Current suite: 108 tests pass. Keep it green.

---

### Task 1: `browser.py` — `snapshot_elements()` + ref resolution

**Files:**
- Modify: `agent/browser.py`
- Test: `tests/test_browser.py`

**Interfaces:**
- Produces:
  - `BrowserSession.snapshot_elements() -> list[dict]` — each `{"ref": "e1", "tag": "a", "role": "", "name": "Logout"}`.
  - `execute_action` now accepts an optional `ref` on the action dict; when present it acts on `[data-agent-ref="{ref}"]`.
  - module constant `MAX_SNAPSHOT_ELEMENTS = 60`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_browser.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_browser.py -k "snapshot or ref" -v`
Expected: FAIL — `snapshot_elements` missing; `ref` not resolved (click called with `None`).

- [ ] **Step 3: Implement in `agent/browser.py`**

Add the constant near `DEFAULT_ACTION_TIMEOUT_MS`:

```python
MAX_SNAPSHOT_ELEMENTS = 60
```

Add this module-level JS constant (after the constants, before the class):

```python
# Collect visible interactive elements, tag each with data-agent-ref="eN",
# and return [{ref, tag, role, name}]. Capped at MAX_SNAPSHOT_ELEMENTS.
_SNAPSHOT_JS = """
(maxN) => {
  const sels = ['button','a[href]','input','textarea','select',
    '[role=button]','[role=link]','[role=tab]','[role=menuitem]',
    '[role=checkbox]','[role=radio]'];
  const seen = new Set();
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
      const name = (el.getAttribute('aria-label') || el.innerText ||
        el.getAttribute('placeholder') || el.value || '').trim().slice(0, 80);
      out.push({ref: ref, tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '', name: name});
      if (out.length >= maxN) return out;
    }
  }
  return out;
}
"""
```

Add the method to `BrowserSession` (after `current_url`):

```python
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
```

Resolve `ref` in `execute_action` — change its head so a `ref` becomes the selector:

```python
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
            raise BrowserError(
                f"{name} failed on {selector!r}: {type(e).__name__}: {e}"
            ) from e
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_browser.py -v`
Expected: PASS (existing browser tests + 4 new).

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all green (108 + 4 = 112).

---

### Task 2: `azure_ai.py` + prompt — feed elements, parse `ref`

**Files:**
- Modify: `agent/azure_ai.py`
- Modify: `prompts/step_translator.txt` (replace contents)
- Test: `tests/test_azure_ai.py`

**Interfaces:**
- Consumes: the element list shape from Task 1 (`{ref, tag, role, name}`).
- Produces: `translate_step(step_text, app_context=None, elements=None)` returns `list[{action, ref, selector, value}]`; `_parse_actions` passes `ref` through.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_azure_ai.py`:

```python
# --- DOM-grounded translate ---------------------------------------------------
import json as _json
from unittest.mock import AsyncMock as _AsyncMock

from agent.azure_ai import AzureAIClient as _Client, _parse_actions as _pa


def test_parse_actions_passes_ref_through():
    raw = _json.dumps({"actions": [{"action": "click", "ref": "e7", "value": None}]})
    out = _pa(raw)
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_azure_ai.py -k "ref or elements or without_elements" -v`
Expected: FAIL — `translate_step` has no `elements` param / ref not in parsed output.

- [ ] **Step 3: Update `agent/azure_ai.py`**

Replace `translate_step` with the elements-aware version:

```python
    async def translate_step(
        self,
        step_text: str,
        app_context: str | None = None,
        elements: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Convert one English test step into ordered Playwright actions.

        When `elements` is provided (the page snapshot), they are listed in the
        prompt and the model must choose targets by `ref`.
        """
        system = self._load_prompt("step_translator.txt")
        user_parts = [f"STEP: {step_text}"]
        if app_context:
            user_parts.append(f"CONTEXT: {app_context}")
        if elements:
            lines = ["PAGE ELEMENTS (choose by ref; only use refs that exist):"]
            for el in elements:
                kind = el.get("role") or el.get("tag") or "?"
                lines.append(f'  {el.get("ref")}  {kind}  "{el.get("name", "")}"')
            user_parts.append("\n".join(lines))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ]
        raw = await self._chat(messages, response_format={"type": "json_object"})
        actions = _parse_actions(raw)
        if not actions:
            raise AzureAIError(f"Translator returned no actions for: {step_text!r}")
        return actions
```

In `_parse_actions`, include `ref` in the emitted dict. Change the append block:

```python
        out.append(
            {
                "action": action,
                "ref": item.get("ref"),
                "selector": item.get("selector"),
                "value": item.get("value"),
            }
        )
```

- [ ] **Step 4: Replace `prompts/step_translator.txt` entirely**

```
You translate plain-English QA test steps into structured Playwright actions
for the Sous Chef Cloud web app.

You are given the test step and PAGE ELEMENTS — the actionable elements on the
page right now, each with a stable "ref" id. Choose elements by their ref.
NEVER invent a selector or a ref that is not in the list.

Return a JSON OBJECT of exactly this shape (no prose, no markdown fences):
  {"actions": [ {"action": "...", "ref": "...", "value": "..."}, ... ]}

Each action:
  - "action": one of navigate | click | fill | select | wait | assert_text | assert_visible
  - "ref": the ref id of the target element. REQUIRED for click, fill, select,
    wait, assert_text, assert_visible. Use only refs from PAGE ELEMENTS.
  - "value": input text for fill/select; expected text for assert_text; the URL
    or relative path for navigate; otherwise null.
  - For "navigate", omit "ref" and put the URL/path in "value".

Rules:
- A step may require several actions (e.g. fill the name, then click Save) —
  list them in order.
- If the element the step needs is not in PAGE ELEMENTS, return the single best
  action you can (for example a "wait" on the closest ref). Do not fabricate.
- If you were told a previous attempt failed, pick a DIFFERENT ref this time.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_azure_ai.py -v`
Expected: PASS (existing azure tests + 3 new).

- [ ] **Step 6: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all green (112 + 3 = 115).

---

### Task 3: `orchestrator.py` — snapshot + heal-once

**Files:**
- Modify: `agent/orchestrator.py` (`_execute_step`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `browser.snapshot_elements()` (Task 1), `azure.translate_step(text, app_context, elements)` (Task 2).
- Produces: a step that snapshots → translates → executes, and on a `BrowserError` re-snapshots + re-translates + retries once before FAILing.

- [ ] **Step 1: Update the orchestrator test fakes + tests**

In `tests/test_orchestrator.py`, add `snapshot_elements` to `_fake_browser`:

```python
def _fake_browser():
    b = MagicMock()
    b.open_session = AsyncMock()
    b.close_session = AsyncMock()
    b.current_url = AsyncMock(return_value="https://app/")
    b.screenshot = AsyncMock(return_value="PNG-B64")
    b.execute_action = AsyncMock()
    b.snapshot_elements = AsyncMock(return_value=[{"ref": "e1", "tag": "a", "role": "link", "name": "Go"}])
    return b
```

Replace `test_action_failure_yields_fail_step_and_case` so it expects the heal-retry (translate twice, still fails):

```python
@pytest.mark.asyncio
async def test_action_failure_retries_once_then_fails():
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
    ]}]
    browser = _fake_browser()
    browser.execute_action = AsyncMock(side_effect=BrowserError("click failed: timeout"))

    azure = _fake_azure(translate_side_effect=[_ok_actions(), _ok_actions()])
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
    )
    state = await orch.run_plan("X")
    step = state.test_cases[0].steps[0]
    assert step.status == "fail"
    assert "click failed" in (step.evaluation or "")
    # healed once: snapshot + translate happened twice
    assert browser.snapshot_elements.await_count == 2
    assert azure.translate_step.await_count == 2
```

Add a new heal-success test:

```python
@pytest.mark.asyncio
async def test_action_failure_heals_and_passes_on_retry():
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Loaded"},
    ]}]
    browser = _fake_browser()
    # first action attempt fails, second (after re-translate) succeeds
    browser.execute_action = AsyncMock(side_effect=[BrowserError("stale"), None])

    azure = _fake_azure(
        translate_side_effect=[_ok_actions(), _ok_actions()],
        evaluate_side_effect=[{"status": "pass", "reason": "Loaded"}],
    )
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
    )
    state = await orch.run_plan("X")
    step = state.test_cases[0].steps[0]
    assert step.status == "pass"
    assert azure.translate_step.await_count == 2  # healed
    assert browser.screenshot.await_count == 1     # reached evaluation
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -k "heals or retries_once" -v`
Expected: FAIL — current orchestrator translates once, doesn't snapshot, doesn't retry.

- [ ] **Step 3: Replace `_execute_step` in `agent/orchestrator.py`**

```python
    async def _execute_step(
        self,
        state: RunState,
        case_id: str,
        step_index: int,
        step: dict[str, Any],
        browser: BrowserSession | None,
        dry_run: bool = False,
    ) -> str:
        """Run one step. Returns 'pass' | 'fail' | 'blocked'.

        Live mode: snapshot the page, translate against its real elements, execute.
        On a BrowserError, re-snapshot + re-translate (telling the model what failed)
        and retry the step ONCE before marking it FAIL.
        """
        action_text = step["action"]
        expected = step.get("expected", "")

        rs_step = Step(action=action_text, detail="translating…", status="running")
        state.add_step(case_id, rs_step)
        self.on_update(state)
        start = time.monotonic()

        # --- dry-run: translate only, no browser -----------------------------
        if dry_run:
            try:
                actions = await self.azure.translate_step(action_text, app_context="dry-run mode")
            except AzureAIError as e:
                duration = time.monotonic() - start
                state.resolve_step(case_id, step_index, "blocked",
                                   f"Could not translate step: {e}", duration)
                self.on_update(state)
                return "blocked"
            rs_step.detail = _format_detail(actions)
            duration = time.monotonic() - start
            state.resolve_step(case_id, step_index, "pass",
                               "[dry-run] translation OK — browser execution skipped", duration)
            self.on_update(state)
            return "pass"

        # --- live: snapshot -> translate -> execute, heal once ---------------
        last_error: str | None = None
        for attempt in (1, 2):
            try:
                elements = await browser.snapshot_elements()
            except Exception:
                elements = []

            context = f"current URL: {await browser.current_url()}"
            if last_error:
                context += (
                    f"\nPrevious attempt failed: {last_error}. "
                    "Pick a different element from the list."
                )

            try:
                actions = await self.azure.translate_step(
                    action_text, app_context=context, elements=elements
                )
            except AzureAIError as e:
                duration = time.monotonic() - start
                state.resolve_step(case_id, step_index, "blocked",
                                   f"Could not translate step: {e}", duration)
                self.on_update(state)
                return "blocked"

            rs_step.detail = _format_detail(actions)
            self.on_update(state)

            try:
                for a in actions:
                    await browser.execute_action(a)
                break  # all actions succeeded — leave the retry loop
            except BrowserError as e:
                last_error = str(e)
                if attempt == 2:
                    duration = time.monotonic() - start
                    state.resolve_step(case_id, step_index, "fail", str(e), duration)
                    self.on_update(state)
                    return "fail"
                # else: loop again — re-snapshot + re-translate with the error

        # --- screenshot + evaluate (unchanged) -------------------------------
        try:
            png_b64 = await browser.screenshot()
            evaluation = await self.azure.evaluate_result(png_b64, expected)
        except (BrowserError, AzureAIError) as e:
            duration = time.monotonic() - start
            state.resolve_step(case_id, step_index, "fail",
                               f"Could not evaluate result: {e}", duration)
            self.on_update(state)
            return "fail"

        status = "pass" if evaluation["status"] == "pass" else "fail"
        duration = time.monotonic() - start
        state.resolve_step(case_id, step_index, status, evaluation["reason"], duration,
                           screenshot_b64=png_b64)
        self.on_update(state)
        return status
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: PASS (all orchestrator tests, including the two heal tests).

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all green (115 + 1 new = 116; the replaced action-failure test keeps the count, +1 heal-success test).

---

### Task 4: Document the grounding in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the `agent/browser.py` and `agent/azure_ai.py` module entries)

**Interfaces:** docs only.

- [ ] **Step 1: Update the `agent/browser.py` entry**

In CLAUDE.md's "Backend modules" section, append to the `agent/browser.py` paragraph:

```markdown
`snapshot_elements()` tags visible interactive elements with `data-agent-ref` and
returns `{ref, tag, role, name}`; actions may carry a `ref` (resolved to
`[data-agent-ref="…"]`) so the model targets real elements instead of guessing CSS.
```

- [ ] **Step 2: Update the `agent/azure_ai.py` entry**

Append to the `agent/azure_ai.py` paragraph:

```markdown
`translate_step` takes the page element snapshot and biases output to choose a
target by `ref`. The orchestrator snapshots before translating and re-snapshots +
re-translates + retries a step once on a browser action failure (DOM-grounded
actions — see the 2026-06-30 spec).
```

- [ ] **Step 3: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: still green (docs-only; 116). Confirm CLAUDE.md reads correctly.

---

## Self-Review

**Spec coverage:**
- Reference-by-id targeting → Task 1 (`snapshot_elements` + ref resolution), Task 2 (prompt + parse). ✓
- Snapshot format `{ref, tag, role, name}`, cap 60 → Task 1. ✓
- `translate_step(elements)` injects list → Task 2. ✓
- Heal once (re-snapshot + re-translate + retry) → Task 3. ✓
- Dry-run path (no browser, no elements) → Task 3 `_execute_step` dry-run branch. ✓
- Evaluation unchanged → Task 3 keeps the screenshot/evaluate block verbatim. ✓
- Snapshot failure → empty list → Task 1 `snapshot_elements` except, Task 3 try/except around snapshot. ✓
- Docs → Task 4. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; every test has real assertions. ✓

**Type consistency:** `snapshot_elements() -> list[dict]` with keys `ref/tag/role/name` used identically in Tasks 1–3. `translate_step(step_text, app_context, elements)` signature consistent across Task 2 (def) and Task 3 (calls). Action dict keys `action/ref/selector/value` consistent between `_parse_actions` (Task 2), `execute_action` (Task 1), and `_ok_actions()` test helper (which uses `selector` — still accepted as fallback, so existing happy-path tests pass). `[data-agent-ref="{ref}"]` selector string identical in Task 1 impl and tests. ✓

**Note on `_ok_actions()`:** the existing helper returns `{"action":"click","selector":"#go","value":None}` (no ref). `execute_action` falls back to `selector` when `ref` is absent, so these keep working — the heal tests rely only on call counts, not on ref resolution.
