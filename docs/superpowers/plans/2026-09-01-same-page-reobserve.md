# Same-Page Re-Observe + Hidden-Child Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent reach nested left-nav items like `Recipe → Edit Inventory`, by re-observing the page after a same-page DOM change and by telling the model which hidden elements exist and what reveals them.

**Architecture:** Two independent edits that only work together. (1) `Orchestrator._attempt_step`'s act→observe loop stops treating "the URL did not change" as "the step is complete": when the next round's snapshot contains element names the previous one lacked, the page revealed new UI and the loop continues. (2) `BrowserSession.snapshot_elements()` gains a second pass reporting DOM-present-but-hidden interactive elements, each naming the visible control that toggles it, with no `ref` of its own — so the model's only available move is clicking that parent.

**Tech Stack:** Python 3.14 (async), Playwright, Azure OpenAI (gpt-5.4-mini translator), pytest with a mocked Playwright `Page`.

**Spec:** `docs/superpowers/specs/2026-09-01-same-page-reobserve-design.md`

## Global Constraints

- Python 3.11+ syntax, async throughout, type hints, docstrings, `logging` never `print`.
- Invoke the interpreter as `.venv/Scripts/python.exe` (Windows) — never a bare `python`.
- `run_state`'s shape must not change. No `FRONTEND.md` edit is needed or wanted.
- `MAX_SNAPSHOT_ELEMENTS = 60` stays as-is. Hidden entries get a **separate** budget, `MAX_HIDDEN_ELEMENTS = 20`, and must never displace a visible element.
- Every cap is enforced **in Python**, regardless of what the page's JS returns — the existing rule for `snapshot_table_data`.
- A hidden entry with no resolvable parent is **dropped**, never emitted ref-less.
- Hidden entries carry `ref: None`, so they must never enter `_attempt_step`'s `seen_elements` map (that map feeds `_format_detail`, whose target-erasure caused the 2026-08-27 false-PASS bug).
- `prompts/step_translator.txt` gets exactly ONE additive rule. Do not reword or delete any existing rule.
- No new env var, no kill-switch flag.
- The suite must not depend on the developer's `.env` (see `tests/conftest.py`).

### Refinement to the spec, adopted here

The spec says the loop "continues" on a reveal. Implement the check at the **top of the next round**, reusing that round's own `snapshot_elements()` call rather than taking an extra one at the bottom. Consequence: a step that reveals nothing costs **one extra DOM query and zero extra model calls**, which is strictly cheaper than the spec's framing while using the identical trigger. Task 5 depends on this.

---

### Task 1: Python-side hidden-entry handling in `snapshot_elements`

The JS pass comes in Task 2. Do the Python guard first: it is where the caps and the drop rule actually live, and it is fully testable against a mocked `Page`.

**Files:**
- Modify: `agent/browser.py` (add `MAX_HIDDEN_ELEMENTS` near `MAX_SNAPSHOT_ELEMENTS` at line 35; rewrite the body of `snapshot_elements` at lines 277-292)
- Test: `tests/test_browser.py` (append to the DOM-snapshot section, after `test_snapshot_elements_empty_on_evaluate_error` at line ~164)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `BrowserSession.snapshot_elements() -> list[dict]`. Entries are either **visible** — `{"ref": "e1", "tag": str, "role": str, "name": str}` (unchanged) — or **hidden** — `{"ref": None, "hidden": True, "parent_ref": "e7", "parent": "Recipe", "tag": str, "role": str, "name": str}`. Visible entries always come first. Task 3 renders these; Task 5 relies on hidden entries having a falsy `ref`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_browser.py`:

```python
@pytest.mark.asyncio
async def test_snapshot_elements_keeps_hidden_children_after_visible_ones():
    """Hidden entries ride along so the model learns a target EXISTS.

    They carry no ref (they are not clickable while hidden) and name the
    visible control that toggles them — the only move available to the model.
    """
    s, page = _session_with_fake_page()
    page.evaluate = AsyncMock(
        return_value=[
            {"ref": "e7", "tag": "a", "role": "", "name": "Recipe"},
            {"ref": None, "hidden": True, "parent_ref": "e7", "parent": "Recipe",
             "tag": "a", "role": "", "name": "Edit Inventory"},
        ]
    )
    out = await s.snapshot_elements()
    assert [e.get("name") for e in out] == ["Recipe", "Edit Inventory"]
    assert out[0]["ref"] == "e7"
    assert out[1]["ref"] is None
    assert out[1]["parent_ref"] == "e7"


@pytest.mark.asyncio
async def test_snapshot_elements_drops_hidden_children_without_a_parent():
    """An unresolvable toggle means an unusable hint. Dropping it beats
    emitting a ref-less entry the model can do nothing with."""
    s, page = _session_with_fake_page()
    page.evaluate = AsyncMock(
        return_value=[
            {"ref": "e1", "tag": "a", "role": "", "name": "Dashboard"},
            {"ref": None, "hidden": True, "parent_ref": "", "parent": "",
             "tag": "a", "role": "", "name": "Orphan"},
            {"ref": None, "hidden": True, "parent_ref": "e1", "parent": "Dashboard",
             "tag": "a", "role": "", "name": "Keeper"},
        ]
    )
    out = await s.snapshot_elements()
    assert [e.get("name") for e in out] == ["Dashboard", "Keeper"]


@pytest.mark.asyncio
async def test_snapshot_elements_caps_hidden_children_separately():
    """Hidden markup must never displace a real, clickable element: the cap is
    its own budget, applied in Python whatever the page's JS returns."""
    s, page = _session_with_fake_page()
    visible = [{"ref": f"e{i}", "tag": "a", "role": "", "name": f"v{i}"} for i in range(5)]
    hidden = [
        {"ref": None, "hidden": True, "parent_ref": "e0", "parent": "v0",
         "tag": "a", "role": "", "name": f"h{i}"}
        for i in range(browser_mod.MAX_HIDDEN_ELEMENTS + 9)
    ]
    page.evaluate = AsyncMock(return_value=visible + hidden)
    out = await s.snapshot_elements()
    kept_visible = [e for e in out if e.get("ref")]
    kept_hidden = [e for e in out if e.get("hidden")]
    assert len(kept_visible) == 5
    assert len(kept_hidden) == browser_mod.MAX_HIDDEN_ELEMENTS


@pytest.mark.asyncio
async def test_snapshot_elements_passes_both_caps_to_the_page():
    """Both budgets reach the JS, so a huge page is trimmed before the payload
    crosses the boundary as well as after."""
    s, page = _session_with_fake_page()
    page.evaluate = AsyncMock(return_value=[])
    await s.snapshot_elements()
    arg = page.evaluate.await_args.args[1]
    assert arg == {
        "maxN": browser_mod.MAX_SNAPSHOT_ELEMENTS,
        "maxHidden": browser_mod.MAX_HIDDEN_ELEMENTS,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```
.venv/Scripts/python.exe -m pytest tests/test_browser.py -k hidden_children -v
.venv/Scripts/python.exe -m pytest tests/test_browser.py -k passes_both_caps -v
```
Expected: FAIL — `AttributeError: module 'agent.browser' has no attribute 'MAX_HIDDEN_ELEMENTS'`.

- [ ] **Step 3: Add the constant**

In `agent/browser.py`, directly after `MAX_SNAPSHOT_ELEMENTS = 60`:

```python
# Hidden interactive elements (a collapsed submenu, a pre-rendered modal) ride
# along on the snapshot so the model learns a target EXISTS and what reveals
# it. Their own budget, applied AFTER the visible pass, so hidden markup can
# never displace a real clickable element out of MAX_SNAPSHOT_ELEMENTS.
MAX_HIDDEN_ELEMENTS = 20
```

- [ ] **Step 4: Rewrite the `snapshot_elements` body**

Replace lines 277-292 of `agent/browser.py` with:

```python
    async def snapshot_elements(self) -> list[dict[str, Any]]:
        """Tag visible interactive elements with data-agent-ref and return them.

        Visible entries: `{ref, tag, role, name}` — the ref is resolvable via
        the selector `[data-agent-ref="<ref>"]`. These come first and are
        unchanged.

        Hidden entries follow: interactive elements that are present in the DOM
        but hidden — a collapsed submenu, a pre-rendered modal — as
        `{ref: None, hidden: True, parent_ref, parent, tag, role, name}`.
        They exist so the model can learn that a target it needs EXISTS and
        which visible control reveals it; without them a step like
        "Recipe > Edit Inventory" is unreachable, because the target is absent
        from the snapshot entirely and the model can only guess among the
        visible items (the TC-1985 failure).

        A hidden entry deliberately carries NO ref: it is not clickable while
        hidden, so handing out a ref would just trade one failure for a
        Playwright timeout. Its only actionable content is `parent_ref`.
        An entry whose toggle could not be resolved is DROPPED — a hint the
        model cannot act on is worse than silence.

        Both caps are enforced here in Python regardless of what the page's JS
        returns (same rule as `snapshot_table_data`). Returns [] if evaluation
        fails.
        """
        if self._page is None:
            raise BrowserError("No active page — call open_session() first")
        try:
            raw = await self._page.evaluate(
                _SNAPSHOT_JS,
                {"maxN": MAX_SNAPSHOT_ELEMENTS, "maxHidden": MAX_HIDDEN_ELEMENTS},
            )
        except Exception as e:  # page closed, JS error, etc.
            log.warning("snapshot_elements failed: %s", e)
            return []
        if not isinstance(raw, list):
            return []

        visible = [e for e in raw if isinstance(e, dict) and e.get("ref")]
        hidden = [
            e
            for e in raw
            if isinstance(e, dict) and not e.get("ref") and e.get("hidden")
            # No resolvable toggle -> unusable hint -> dropped.
            and e.get("parent_ref")
        ]
        if len(visible) >= MAX_SNAPSHOT_ELEMENTS:
            log.warning("Element snapshot truncated to %d", MAX_SNAPSHOT_ELEMENTS)
        if len(hidden) > MAX_HIDDEN_ELEMENTS:
            log.warning("Hidden element list truncated to %d", MAX_HIDDEN_ELEMENTS)
        return visible[:MAX_SNAPSHOT_ELEMENTS] + hidden[:MAX_HIDDEN_ELEMENTS]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_browser.py -q`
Expected: PASS, all of them — including the two pre-existing snapshot tests at lines 145 and 160, which still hold because visible entries pass through untouched.

- [ ] **Step 6: Commit**

```bash
git add agent/browser.py tests/test_browser.py
git commit -m "feat: snapshot reports hidden children with the control that reveals them"
```

---

### Task 2: The JS hidden pass

**Files:**
- Modify: `agent/browser.py` — `_SNAPSHOT_JS`, the signature line at 47 and the `return out;` at line 127
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: Task 1's `{"maxN", "maxHidden"}` argument object and the entry shapes it filters on.
- Produces: no new Python symbol. The JS must emit hidden entries exactly in Task 1's shape, or Task 1's filter drops them.

**Honest limitation, state it in the commit:** a mocked `Page` cannot execute this JS, so these tests assert the *contract* (signature destructuring, both caps used) and the live run in Task 6 is what actually validates the DOM heuristic. Do not claim otherwise.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_snapshot_js_destructures_both_caps():
    """The JS takes ONE argument object — Playwright passes a single value —
    and must read both budgets out of it, or the hidden pass is unbounded."""
    js = browser_mod._SNAPSHOT_JS
    assert "({maxN, maxHidden})" in js
    assert "maxHidden" in js.split("// ---- hidden children")[1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_browser.py -k destructures -v`
Expected: FAIL — `IndexError: list index out of range` (the marker comment does not exist yet).

- [ ] **Step 3: Change the JS signature**

In `agent/browser.py`, line 47, replace `(maxN) => {` with:

```javascript
({maxN, maxHidden}) => {
```

- [ ] **Step 4: Add the hidden pass**

Replace the final `  return out;` of `_SNAPSHOT_JS` (line 127, immediately before the closing `}`) with:

```javascript
  // ---- hidden children -------------------------------------------------
  // Interactive elements PRESENT in the DOM but hidden — a collapsed submenu,
  // a pre-rendered modal. They get no ref (not clickable yet); each names the
  // visible control that toggles it, which is the model's only move. An
  // element whose toggle cannot be resolved is skipped, never emitted with an
  // empty parent_ref: Python drops those anyway, and a hint nobody can act on
  // is worse than silence.
  const hidden = [];
  for (const sel of sels) {
    if (hidden.length >= maxHidden) break;
    for (const el of document.querySelectorAll(sel)) {
      if (hidden.length >= maxHidden) break;
      if (seen.has(el) || tagged.has(el)) continue;
      seen.add(el);
      const r = el.getBoundingClientRect();
      const st = window.getComputedStyle(el);
      if (r.width > 0 && r.height > 0 &&
          st.visibility !== 'hidden' && st.display !== 'none') continue;
      const name = (el.getAttribute('aria-label') || el.getAttribute('title') ||
        el.textContent || '').trim();
      if (!name) continue;
      // The nearest ancestor actually doing the hiding. If there is none, the
      // element is hidden for some other reason (0x0, clipped, off-screen) and
      // there is no toggle to name.
      let hider = null;
      for (let box = el.parentElement; box && box !== document.body; box = box.parentElement) {
        const bs = window.getComputedStyle(box);
        if (bs.display === 'none' || bs.visibility === 'hidden') { hider = box; break; }
      }
      if (!hider) continue;
      // The toggle is the nearest ALREADY-TAGGED (therefore visible) control
      // preceding that container: walk previous siblings, then climb.
      let toggle = null;
      for (let node = hider; node && node !== document.body && !toggle; node = node.parentElement) {
        for (let sib = node.previousElementSibling; sib && !toggle; sib = sib.previousElementSibling) {
          if (sib.hasAttribute('data-agent-ref')) toggle = sib;
          else toggle = sib.querySelector('[data-agent-ref]');
        }
      }
      if (!toggle) continue;
      hidden.push({ref: null, hidden: true,
                   parent_ref: toggle.getAttribute('data-agent-ref'),
                   parent: ((toggle.innerText || toggle.getAttribute('aria-label') ||
                             '').trim()).slice(0, 40),
                   tag: el.tagName.toLowerCase(),
                   role: el.getAttribute('role') || '',
                   name: name.slice(0, 80)});
    }
  }
  return out.concat(hidden);
```

Note the two `if (out.length >= maxN) return out;` early exits in the visible pass are left alone: a page that fills the visible budget skips hidden discovery entirely. That is the correct trade — clickable elements outrank hints.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_browser.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/browser.py tests/test_browser.py
git commit -m "feat: DOM pass finds hidden interactive elements and their toggle

Parent resolution is a heuristic that a mocked Page cannot exercise; the
live TC-1985 run is what validates it."
```

---

### Task 3: Render hidden elements in the translator prompt

**Files:**
- Modify: `agent/azure_ai.py:137-142` (the `if elements:` block in `translate_step`)
- Test: `tests/test_azure_ai.py`

**Interfaces:**
- Consumes: Task 1's entry shapes.
- Produces: no new symbol. The rendered block is a `HIDDEN ELEMENTS (...)` section appended after `PAGE ELEMENTS`; Task 4's prompt rule refers to it by that exact name.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_azure_ai.py`, next to `test_translate_step_includes_elements_in_prompt`
(line ~360) and following its exact style — a `_Client` with `_chat` swapped for a
capturing `fake_chat`. Do NOT invent new helpers:

```python
@pytest.mark.asyncio
async def test_translate_step_renders_hidden_elements_separately():
    """PAGE ELEMENTS keeps its exact shape — choosable refs only — while hidden
    entries appear in their own block naming the parent to click."""
    client = _Client(endpoint="https://x", api_key="k", deployment="gpt-4o")
    captured = {}

    async def fake_chat(messages, **kw):
        captured["messages"] = messages
        return _json.dumps({"actions": []})

    client._chat = fake_chat  # type: ignore
    await client.translate_step(
        "Go to Recipe > Edit Inventory",
        app_context="url: /x",
        elements=[
            {"ref": "e7", "tag": "a", "role": "", "name": "Recipe"},
            {"ref": None, "hidden": True, "parent_ref": "e7", "parent": "Recipe",
             "tag": "a", "role": "", "name": "Edit Inventory"},
        ],
    )
    sent = captured["messages"][-1]["content"]
    assert "PAGE ELEMENTS" in sent
    assert "HIDDEN ELEMENTS" in sent
    page_block, hidden_block = sent.split("HIDDEN ELEMENTS")
    # The hidden child must never look like a choosable ref.
    assert "Edit Inventory" not in page_block
    assert "e7" in page_block
    assert "Edit Inventory" in hidden_block
    assert "e7" in hidden_block


@pytest.mark.asyncio
async def test_translate_step_omits_the_hidden_block_when_there_are_none():
    """A page with nothing hidden must produce the prompt it produces today —
    not an empty section inviting the model to invent one."""
    client = _Client(endpoint="https://x", api_key="k", deployment="gpt-4o")
    captured = {}

    async def fake_chat(messages, **kw):
        captured["messages"] = messages
        return _json.dumps({"actions": []})

    client._chat = fake_chat  # type: ignore
    await client.translate_step(
        "Click Save",
        app_context="url: /x",
        elements=[{"ref": "e1", "tag": "button", "role": "", "name": "Save"}],
    )
    assert "HIDDEN ELEMENTS" not in captured["messages"][-1]["content"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_azure_ai.py -k hidden -v`
Expected: FAIL — `AssertionError: assert 'HIDDEN ELEMENTS' in ...`.

- [ ] **Step 3: Implement the rendering**

In `agent/azure_ai.py`, replace the `if elements:` block (lines 137-142) with:

```python
        if elements:
            # Visible, choosable elements keep PAGE ELEMENTS exactly as it has
            # always been — nothing the prompt relies on moves.
            visible = [e for e in elements if e.get("ref")]
            hidden = [e for e in elements if not e.get("ref") and e.get("hidden")]
            if visible:
                lines = ["PAGE ELEMENTS (choose by ref; only use refs that exist):"]
                for el in visible:
                    kind = el.get("role") or el.get("tag") or "?"
                    lines.append(f'  {el.get("ref")}  {kind}  "{el.get("name", "")}"')
                user_parts.append("\n".join(lines))
            # Hidden children are informational: they prove a target EXISTS and
            # name what reveals it. Deliberately ref-less, so the only action
            # available is clicking the parent.
            if hidden:
                lines = [
                    "HIDDEN ELEMENTS (present but not visible — NOT clickable "
                    "yet; click the parent ref to reveal, then you will be "
                    "called again with a fresh list):"
                ]
                for el in hidden:
                    lines.append(
                        f'  under {el.get("parent_ref")} "{el.get("parent", "")}"'
                        f'  ->  "{el.get("name", "")}"'
                    )
                user_parts.append("\n".join(lines))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_azure_ai.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/azure_ai.py tests/test_azure_ai.py
git commit -m "feat: translator prompt gets a HIDDEN ELEMENTS block"
```

---

### Task 4: The translator prompt rule

**Files:**
- Modify: `prompts/step_translator.txt`

**Interfaces:**
- Consumes: the exact block name `HIDDEN ELEMENTS` from Task 3.
- Produces: nothing in code.

- [ ] **Step 1: Add exactly one rule**

In `prompts/step_translator.txt`, insert immediately **after** the existing rule that begins `- Every element in PAGE ELEMENTS is ALREADY visible on screen right now.` and ends `menu toggles collapse an open menu and hide the target.`:

```
- If a HIDDEN ELEMENTS block is present, those items EXIST in the page but are
  not visible and cannot be clicked yet — they have no ref on purpose. If the
  CURRENT step's target is listed there (for example "Edit Inventory" under
  "Recipe"), your plan for this round is to click its PARENT ref to reveal it.
  You will then be called again with a fresh PAGE ELEMENTS list containing the
  target, and you click it then. Never guess a different visible menu item
  because the target is missing from PAGE ELEMENTS, and never invent a ref for
  a hidden item.
```

Do not touch any other rule. In particular leave the "never click a parent menu/tab to reveal an element that is already listed" sentence exactly as it is — it governs elements *already listed*, which is a different case, and the two rules coexist deliberately.

- [ ] **Step 2: Verify no other rule changed**

Run: `git diff --stat prompts/step_translator.txt`
Expected: `1 file changed, 10 insertions(+)` — insertions only, zero deletions. If any line shows as deleted, revert and redo the insert.

- [ ] **Step 3: Commit**

```bash
git add prompts/step_translator.txt
git commit -m "feat: translator rule for revealing a hidden target via its parent"
```

---

### Task 5: Same-page re-observe in the act→observe loop

The load-bearing task. Everything above is inert without it.

**Files:**
- Modify: `agent/orchestrator.py` — `_attempt_step`: the round-top snapshot at lines 795-804, and the loop-bottom exit at lines 908-911
- Test: `tests/test_orchestrator.py` (append near `test_navigation_triggers_reobserve_with_progress` at line 1074)

**Interfaces:**
- Consumes: Task 1's entry shapes (hidden entries have falsy `ref`).
- Produces: no new public symbol. Two new locals inside `_attempt_step`: `prev_names: set[tuple[str, str]] | None` and `check_for_reveal: bool`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_same_page_reveal_reobserves_instead_of_ending_the_step():
    """The TC-1985 fix. Clicking a collapsible nav parent expands a submenu
    WITHOUT navigating, so the old `if not navigated: break` ended the step
    before the model ever saw the revealed child — it could only guess among
    the visible top-level items. A snapshot carrying names the previous one
    lacked now continues the loop."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Go to Recipe > Edit Inventory", "expected": "Inventory page"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[
            [{"action": "click", "ref": "e7", "value": None}],   # click Recipe (no nav)
            [{"action": "click", "ref": "e9", "value": None}],   # click the revealed child
            [],                                                  # done
        ],
        evaluate_side_effect=[{"status": "pass", "reason": "on the inventory page"}],
    )
    browser = _fake_browser()
    browser.snapshot_elements = AsyncMock(side_effect=[
        # round 1: the submenu is collapsed
        [{"ref": "e7", "tag": "a", "role": "", "name": "Recipe"}],
        # round 2: the click revealed a child -> a NEW name -> keep going
        [{"ref": "e7", "tag": "a", "role": "", "name": "Recipe"},
         {"ref": "e9", "tag": "a", "role": "", "name": "Edit Inventory"}],
        # round 3: nothing new after the child click
        [{"ref": "e9", "tag": "a", "role": "", "name": "Edit Inventory"}],
    ])
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")
    step = state.test_cases[0].steps[0]
    assert step.status == "pass"
    # It got a SECOND chance to plan, which is the whole point.
    assert azure.translate_step.await_count >= 2
    ctx2 = azure.translate_step.call_args_list[1].kwargs["app_context"]
    assert "PROGRESS" in ctx2


@pytest.mark.asyncio
async def test_same_page_with_nothing_revealed_keeps_the_fast_path():
    """An ordinary same-page step (fill, click Save) must still cost exactly
    ONE model call. The re-observe check reuses the next round's own snapshot,
    so a no-reveal step pays one cheap DOM query and no extra translate."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Saved"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[_ok_actions()],
        evaluate_side_effect=[{"status": "pass", "reason": "saved"}],
    )
    browser = _fake_browser()  # returns the SAME single element every call
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")
    assert state.test_cases[0].steps[0].status == "pass"
    assert azure.translate_step.await_count == 1


@pytest.mark.asyncio
async def test_expand_then_collapse_terminates():
    """Clicking a toggle twice collapses it again, which adds no new name, so
    the loop must end rather than oscillate."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Open the Recipe menu", "expected": "Submenu shown"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[
            [{"action": "click", "ref": "e7", "value": None}],
            [{"action": "click", "ref": "e7", "value": None}],
            [{"action": "click", "ref": "e7", "value": None}],
        ],
        evaluate_side_effect=[{"status": "pass", "reason": "ok"}],
    )
    browser = _fake_browser()
    expanded = [{"ref": "e7", "tag": "a", "role": "", "name": "Recipe"},
                {"ref": "e9", "tag": "a", "role": "", "name": "Edit Inventory"}]
    collapsed = [{"ref": "e7", "tag": "a", "role": "", "name": "Recipe"}]
    browser.snapshot_elements = AsyncMock(side_effect=[collapsed, expanded, collapsed])
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")
    assert state.test_cases[0].steps[0].status == "pass"
    # Round 3's snapshot re-collapsed: no new name, so no 4th translate.
    assert azure.translate_step.await_count == 3


@pytest.mark.asyncio
async def test_hidden_entries_never_reach_the_performed_action_detail():
    """seen_elements feeds _format_detail, whose target-erasure caused the
    2026-08-27 false PASSes. A ref-less hidden entry must never enter it and
    must never be named as something the agent performed."""
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Go to Recipe > Edit Inventory", "expected": "Inventory"},
    ]}]
    azure = _fake_azure(
        translate_side_effect=[[{"action": "click", "ref": "e7", "value": None}], []],
        evaluate_side_effect=[{"status": "pass", "reason": "ok"}],
    )
    browser = _fake_browser()
    snap = [
        {"ref": "e7", "tag": "a", "role": "", "name": "Recipe"},
        {"ref": None, "hidden": True, "parent_ref": "e7", "parent": "Recipe",
         "tag": "a", "role": "", "name": "Edit Inventory"},
    ]
    browser.snapshot_elements = AsyncMock(return_value=snap)
    orch = Orchestrator(
        azure=azure,
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
    state = await orch.run_single_case("A")
    step = state.test_cases[0].steps[0]
    assert "Recipe" in step.detail
    assert "Edit Inventory" not in step.detail
    performed = azure.evaluate_result.call_args.kwargs["performed"]
    assert "Edit Inventory" not in performed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py -k "reveal or fast_path or terminates or performed_action_detail" -v`
Expected: the reveal test FAILS (`assert 1 >= 2` — the loop broke after round 1). The other three may already pass; that is fine and expected — they are guard-rails proving the change does not regress today's behaviour.

- [ ] **Step 3: Track the previous round's names**

In `agent/orchestrator.py`, immediately before the `for _round in range(max_rounds):` line (which follows the `seen_elements` declaration at line 788), add:

```python
        # The (tag, name) pairs the PREVIOUS round planned from. Compared per
        # round to spot a same-page reveal — a submenu expanding, a modal
        # opening. Compared by NAME, never by ref: refs are assigned per
        # snapshot and renumber every time, so every element would look new.
        prev_names: set[tuple[str, str]] | None = None
        # Set when a round ran entirely on one page. The next round's own
        # snapshot then decides: new names = the page revealed something, keep
        # going; nothing new = the step really is done.
        check_for_reveal = False
```

- [ ] **Step 4: Decide at the top of the round**

In the same method, replace this block (lines 795-804):

```python
            actions_before = len(executed_actions)

            try:
                elements = await browser.snapshot_elements()
            except Exception:
                elements = []
            for _el in elements:
                if _el.get("ref"):
                    seen_elements[_el["ref"]] = _el
```

with:

```python
            actions_before = len(executed_actions)

            try:
                elements = await browser.snapshot_elements()
            except Exception:
                elements = []
            # Hidden entries carry ref=None, so this guard also keeps them out
            # of seen_elements — which _format_detail reads to name what was
            # clicked. Naming something the agent never touched is exactly the
            # class of bug that caused the 2026-08-27 false PASSes.
            for _el in elements:
                if _el.get("ref"):
                    seen_elements[_el["ref"]] = _el

            # A same-page round just finished. Its actions either revealed new
            # UI (a submenu expanded, a modal opened) or the step is complete.
            # Deciding HERE, from this round's own snapshot, is what makes the
            # check nearly free: a step that revealed nothing pays one DOM
            # query and NO model call.
            # VISIBLE elements only (`e.get("ref")`). A hidden entry carries the
            # same (tag, name) as the element it becomes once revealed, so
            # counting hidden entries here would mean "Edit Inventory" was
            # already known in round 1 and its appearance in round 2 registered
            # as nothing new — the loop would break and TC-1985 would still
            # fail. The reveal we are detecting is precisely a hidden element
            # BECOMING visible.
            names = {
                (e.get("tag") or "", e.get("name") or "")
                for e in elements
                if e.get("ref")
            }
            if check_for_reveal:
                revealed = names - (prev_names or set())
                if not revealed:
                    break  # nothing new on the page — the step is done
                log.info(
                    "Same-page reveal on step %d of %s: %d new element(s) — re-observing",
                    orig_index + 1, case_id, len(revealed),
                )
                # The reveal itself is evidence the evaluator needs (an open
                # submenu is transient), and the action that caused it got no
                # frame: it was the last of a same-page plan.
                try:
                    await browser.wait_for_settle(quiet_ms=400, timeout_ms=3_000)
                    frames.append(await browser.screenshot())
                except Exception:
                    pass  # a lost frame never fails the step
            check_for_reveal = False
            prev_names = names
```

- [ ] **Step 5: Replace the loop-bottom exit**

Replace lines 908-911:

```python
            if len(executed_actions) >= max_actions:
                break
            if not navigated:
                break  # whole plan ran on one page — step complete (fast path)
```

with:

```python
            if len(executed_actions) >= max_actions:
                break
            if not navigated:
                # "The URL did not change" is NOT the same fact as "the step is
                # complete", and conflating them is what made TC-1985
                # unreachable: clicking a collapsible nav parent expands a
                # submenu without navigating, so the step ended before the
                # model ever saw the child it needed. Let the next round's
                # snapshot settle it (see check_for_reveal above); when nothing
                # was revealed it breaks there, one cheap DOM query later.
                check_for_reveal = True
                continue
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py -q`
Expected: PASS. Pay attention to `test_navigation_triggers_reobserve_with_progress` (line 1074) and `test_act_observe_loop_is_round_capped` (line 1116): both must still pass untouched. The former navigates every round so `check_for_reveal` never sets; the latter is bounded by `max_rounds`, which this change does not alter.

If a pre-existing test now fails because a fake's `snapshot_elements` side_effect list is exhausted, that fake needs one more entry — the loop legitimately takes one more snapshot. Extend the fake; do NOT weaken the new behaviour to fit an old fake.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 433 + the new tests, all passing, zero failures.

- [ ] **Step 8: Commit**

```bash
git add agent/orchestrator.py tests/test_orchestrator.py
git commit -m "fix: re-observe after a same-page reveal, not only after a navigation"
```

---

### Task 6: Documentation and live verification

**Files:**
- Modify: `CLAUDE.md` (the `agent/browser.py` and `agent/orchestrator.py` module sections)

- [ ] **Step 1: Document the snapshot change**

In `CLAUDE.md`'s `### agent/browser.py` section, after the `snapshot_table_data()` paragraph, add:

```markdown
`snapshot_elements()` also returns **hidden** interactive elements
(2026-09-01) — DOM-present but invisible, e.g. a collapsed submenu or a
pre-rendered modal — as `{ref: None, hidden: True, parent_ref, parent, tag,
role, name}`, listed after the visible ones. They exist so the model can
learn a target EXISTS and which visible control reveals it: without them a
step like `Recipe → Edit Inventory` is unreachable, because the target is
absent from the snapshot and the model can only guess among the visible items
(the TC-1985 failure). They deliberately carry **no ref** — a hidden element
is not clickable, so a ref would only buy a Playwright timeout; `parent_ref`
is the sole actionable field. An entry whose toggle cannot be resolved is
DROPPED rather than emitted ref-less. Separate budget,
`MAX_HIDDEN_ELEMENTS`=20, applied in Python AFTER the visible pass so hidden
markup can never displace a real clickable element; a page that fills
`MAX_SNAPSHOT_ELEMENTS` skips hidden discovery entirely. Parent resolution is
a DOM heuristic (nearest `display:none` ancestor, then the nearest tagged
control preceding it) — it cannot be exercised by the mocked-Page tests, so
the live run is its only real validation.
```

- [ ] **Step 2: Document the loop change**

In `CLAUDE.md`'s `### agent/orchestrator.py` section, add:

```markdown
**Same-page re-observe (2026-09-01).** The act→observe loop used to end a
round only on a URL change (`if not navigated: break`), which conflated two
different facts: "the page did not change" and "a single-page plan is
complete". Clicking a collapsible nav parent expands a submenu WITHOUT
navigating, so the step ended before the model was ever shown the revealed
child — TC-1985 (`Recipe → Edit Inventory`) could never pass, and the agent
clicked `Help`, `API Key`, `Recipe`, `Recipe` instead. Now a same-page round
sets `check_for_reveal`, and the NEXT round's own snapshot decides: if it
holds `(tag, name)` pairs the previous one lacked, the page revealed new UI
and the loop continues; otherwise it breaks there. Compared by name, never by
ref — refs renumber per snapshot, so every element would read as new. Because
the check reuses a snapshot the loop was taking anyway, a step that reveals
nothing costs one cheap DOM query and **zero** model calls; only a real
reveal pays a translate. `max_rounds`=6 and `step_attempt_budget_s` remain
the backstops, and expand-then-collapse converges (the collapse adds no new
name). A reveal also appends one frame, because the revealing action was the
last of a same-page plan and so got none, and an open submenu is transient
evidence the evaluator needs.
```

- [ ] **Step 3: Run the full suite one more time**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 4: Live-verify against TC-1985**

```powershell
$env:HEADLESS="false"; .venv\Scripts\python.exe main.py --testcase SOUSCLOUD-TC-1985
```

Confirm in the log and the window:
- a `Same-page reveal on step N of SOUSCLOUD-TC-1985` line appears;
- the browser reaches `/account/recipes` (breadcrumb `> Recipe > Edit Inventory`);
- the step's `performed` detail names real targets, e.g. `click 'Recipe' (a); click 'Edit Inventory' (a)` — never a bare `click; click`.

**If the reveal line never appears,** the parent-resolution heuristic did not match the real sidebar markup. Do not patch around it blind: capture the sidebar's actual HTML with
`.venv\Scripts\python.exe -c` + `snapshot_elements`, or Playwright MCP against the app, and reshape the Task 2 JS against what is really there. This is the risk the spec flagged.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record same-page re-observe and hidden-child discovery"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Cause 1 — loop never re-observes | 5 |
| Cause 2 — model cannot know a hidden child exists | 1, 2, 3, 4 |
| Loop change: `(tag, name)` comparison, not refs | 5 (Step 3) |
| Inner action loop untouched | 5 — no step modifies it; verified by `test_navigation_triggers_reobserve_with_progress` |
| Fast path preserved | 5 (`test_same_page_with_nothing_revealed_keeps_the_fast_path`) |
| `max_rounds` / budget unchanged as backstops | 5 (Step 6 checks `test_act_observe_loop_is_round_capped`) |
| Termination / convergence | 5 (`test_expand_then_collapse_terminates`) |
| Reveal frame captured, not one-per-action | 5 (Step 4) |
| Hidden entries: no ref | 1, 2 |
| Unresolvable parent dropped | 1 (`..._drops_hidden_children_without_a_parent`), 2 |
| `MAX_HIDDEN_ELEMENTS`=20, separate, after visible | 1 (`..._caps_hidden_children_separately`) |
| Caps enforced in Python | 1 (Step 4) |
| Safe at the `_format_detail` boundary | 5 (`test_hidden_entries_never_reach_the_performed_action_detail`) |
| `HIDDEN ELEMENTS` rendering, PAGE ELEMENTS unchanged | 3 |
| One additive prompt rule, nothing reworded | 4 (Step 2 enforces insertions-only) |
| Unit test table (8 rows) | Tasks 1, 2, 3, 5 — all 8 covered |
| Live TC-1985 run | 6 (Step 4) |
| `CLAUDE.md` updated, no `FRONTEND.md` change | 6 |
| Risk 1 — heuristic needs live validation | 2 (commit message), 6 (Step 4 fallback) |

No gaps.

**Placeholder scan:** none — every code step carries the actual code, every run step the actual command and expected result.

**Type consistency:** the entry dict keys (`ref`, `hidden`, `parent_ref`, `parent`, `tag`, `role`, `name`) are identical across Tasks 1, 2, 3 and 5. `MAX_HIDDEN_ELEMENTS` is defined in Task 1 and referenced by name in 1, 2, 6. `prev_names` / `check_for_reveal` are declared and used only within Task 5.

**One deviation from the spec, deliberate and documented above:** the reveal check sits at the top of the next round rather than the bottom of the current one, which removes the extra model call the spec's framing implied. Same trigger, strictly cheaper.
