# Live-Run Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Manual panel's context into the Live run — run-level login with `.env` fallback, precondition and test data in the tape, a pause after each browser launch, and a URL banner stamped onto every screenshot.

**Architecture:** Four independent slices over the existing agent loop. A new pure module (`agent/url_banner.py`) composites a URL strip onto PNG bytes and is called from the single chokepoint `BrowserSession.screenshot()`, so every frame gets it with no orchestrator change. The `run_state` contract grows three fields carried straight from the QMetry case dicts the orchestrator already holds. Credentials reach the orchestrator as a run-level pair plus an optional per-case override map, keeping `ManualStore` out of `agent/`.

**Tech Stack:** Python 3.14 (async, `httpx`, Playwright, FastAPI, pytest), Pillow (new), React 18 + Vite, hand-written CSS.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-live-run-parity-design.md`.
- Python invoked as `.venv\Scripts\python.exe` (Windows) — never bare `python`.
- Credentials travel **inbound only**. Never in `run_state`, never in a response body, never in a prompt or log line, never in an SSE event.
- Changing the `run_state` shape requires FRONTEND.md, `agent/run_state.py`, the frontend hook/components, **both** fixture copies, and `tests/test_run_state.py` to change in the same commit (CLAUDE.md rule).
- A cosmetic failure must never fail a step: banner errors return the raw screenshot.
- `logging`, never `print`. Type hints and docstrings on new code.
- The full suite must stay green: `.venv\Scripts\python.exe -m pytest tests/ -q` (284 tests at plan time).
- The frontend has no JS test runner (`npm run dev|build|preview` only). Frontend tasks verify with `npm run build` plus a stated manual check.
- Launch delay default `3.0`s, `0` disables, skipped when `HEADLESS=true`.
- Banner height `32`px; page pixels are never moved or covered.

---

### Task 1: URL banner module

**Files:**
- Create: `agent/url_banner.py`
- Modify: `requirements.txt`
- Test: `tests/test_url_banner.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `stamp_url(png_bytes: bytes, url: str) -> bytes` — returns a new PNG `BANNER_HEIGHT` px taller with the URL drawn above the original pixels; returns `png_bytes` unchanged if anything goes wrong. Also exports `BANNER_HEIGHT: int = 32`. Task 2 imports both.

- [ ] **Step 1: Add Pillow to requirements and install it**

Append to `requirements.txt` after the `Jinja2>=3.1.4` line:

```
Pillow>=10.3.0               # composite the URL banner onto screenshots
```

Run: `.venv\Scripts\python.exe -m pip install -r requirements.txt`
Expected: Pillow installs. The venv's `pip.ini` already handles Duke's TLS inspection — do not add `--trusted-host`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_url_banner.py`:

```python
"""stamp_url tests — pure image work, no browser involved."""

from __future__ import annotations

import io

from agent.url_banner import BANNER_HEIGHT, stamp_url


def _png(width: int = 200, height: int = 100) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    return Image.open(io.BytesIO(png)).size


def test_stamp_url_grows_height_keeps_width():
    out = stamp_url(_png(200, 100), "https://test.souscheftech.com/account/recipes")
    assert _size(out) == (200, 100 + BANNER_HEIGHT)


def test_stamp_url_returns_a_valid_png():
    out = stamp_url(_png(), "https://example.com/x")
    assert out.startswith(b"\x89PNG")


def test_stamp_url_returns_input_unchanged_on_bad_bytes():
    junk = b"not-a-png-at-all"
    assert stamp_url(junk, "https://example.com") == junk


def test_stamp_url_tolerates_empty_url():
    out = stamp_url(_png(200, 100), "")
    assert _size(out) == (200, 100 + BANNER_HEIGHT)


def test_stamp_url_tolerates_a_very_long_url():
    long_url = "https://test.souscheftech.com/" + "segment/" * 60
    out = stamp_url(_png(200, 100), long_url)
    assert _size(out) == (200, 100 + BANNER_HEIGHT)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_url_banner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.url_banner'`

- [ ] **Step 4: Write the implementation**

Create `agent/url_banner.py`:

```python
"""Stamp the page URL onto a screenshot.

Playwright's `page.screenshot()` captures the page viewport only — it cannot
include Chrome's real address bar — so the URL is drawn into a strip ABOVE the
page pixels. The page image itself is never modified or covered.

Pure and browser-free, so it unit-tests without Playwright. Any failure returns
the original bytes: a cosmetic banner must never fail a step.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

BANNER_HEIGHT = 32
_BG = (32, 33, 36)        # Chrome's dark toolbar
_FG = (232, 234, 237)
_PAD_X = 12
_FONT_SIZE = 14
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def stamp_url(png_bytes: bytes, url: str) -> bytes:
    """Return a new PNG with `url` drawn in a strip above the page pixels.

    Returns `png_bytes` unchanged if Pillow is missing, the input is not a
    decodable image, or drawing raises for any other reason.
    """
    try:
        from PIL import Image, ImageDraw

        src = Image.open(io.BytesIO(png_bytes))
        src.load()
        src = src.convert("RGB")

        out = Image.new("RGB", (src.width, src.height + BANNER_HEIGHT), _BG)
        out.paste(src, (0, BANNER_HEIGHT))

        draw = ImageDraw.Draw(out)
        font = _load_font()
        text = _fit(draw, font, url or "(no url)", src.width - 2 * _PAD_X)
        # Fixed y rather than anchor="lm": PIL's default bitmap font rejects
        # the anchor kwarg, which would send every capture down the fallback.
        draw.text((_PAD_X, (BANNER_HEIGHT - _FONT_SIZE) // 2), text, fill=_FG, font=font)

        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        log.debug("URL banner skipped; returning raw screenshot", exc_info=True)
        return png_bytes


def _load_font():
    """A real TrueType face if one is installed, else PIL's bitmap fallback."""
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, _FONT_SIZE)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit(draw, font, text: str, max_width: int) -> str:
    """Trim `text` with a trailing ellipsis until it fits `max_width` pixels."""
    if max_width <= 0:
        return ""

    def width_of(candidate: str) -> float:
        try:
            return draw.textlength(candidate, font=font)
        except Exception:
            return len(candidate) * _FONT_SIZE * 0.6

    if width_of(text) <= max_width:
        return text
    trimmed = text
    while trimmed and width_of(trimmed + "…") > max_width:
        trimmed = trimmed[:-1]
    return trimmed + "…" if trimmed else ""
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_url_banner.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add agent/url_banner.py tests/test_url_banner.py requirements.txt
git commit -m "feat: add stamp_url — composite a URL strip above a screenshot"
```

---

### Task 2: Stamp every captured frame

**Files:**
- Modify: `agent/browser.py:182-187` (`screenshot`), plus the import block near `agent/browser.py:1-30`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `stamp_url`, `BANNER_HEIGHT` from Task 1.
- Produces: `BrowserSession.screenshot()` still returns a base64 PNG `str`. Every caller — the per-action frames in `_execute_step` and the final stored frame — gets the banner with no change of its own.

**Note:** existing tests stub `page.screenshot` with `b"\x89PNG-bytes"`, which is not a decodable image, so `stamp_url` returns it unchanged and those assertions keep passing. That is the graceful-fallback path doing its job, not an accident to rely on — the new test below covers the real compositing path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_browser.py`:

```python
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
```

Add `import base64` to the test module's imports if it is not already there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_browser.py -k stamp -v`
Expected: FAIL — `AttributeError: <module 'agent.browser'> has no attribute 'stamp_url'`

- [ ] **Step 3: Write the implementation**

Add to the import block in `agent/browser.py`, beside the other `agent.` imports:

```python
from agent.url_banner import stamp_url
```

Replace `screenshot` (`agent/browser.py:182-187`) with:

```python
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
```

`agent/browser.py:28` already defines the module-level `log`, and `base64` is already imported — no import changes beyond `stamp_url`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_browser.py -v`
Expected: all pass, including the two new ones.

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 291 passed (284 + 5 from Task 1 + 2 here)

- [ ] **Step 6: Commit**

```bash
git add agent/browser.py tests/test_browser.py
git commit -m "feat: stamp the page URL onto every captured frame"
```

---

### Task 3: Launch delay after each browser opens

**Files:**
- Modify: `agent/orchestrator.py:41-65` (`__init__`), `agent/orchestrator.py:171-187` (`_execute_case` browser setup)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Orchestrator(..., launch_delay_s: float | None = None)`. Env `AGENT_LAUNCH_DELAY_S` (default `3`). The delay fires only when the session is non-headless and the value is `> 0`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`. This uses the module's real helpers: `FakeCaseSource(plan, cases)`, `_fake_browser()`, `_fake_azure(...)`, `_ok_actions()`. First add one shared builder beside them:

```python
def _delay_orchestrator(launch_delay_s: float, headless: bool):
    """One-case orchestrator whose fake session reports a known headless flag."""
    browser = _fake_browser()
    browser.headless = headless
    cases = [{"id": "A", "name": "Alpha", "steps": [
        {"action": "Click go", "expected": "Page loaded"},
    ]}]
    orch = Orchestrator(
        azure=_fake_azure(
            translate_side_effect=[_ok_actions()],
            evaluate_side_effect=[{"status": "pass", "reason": "Loaded"}],
        ),
        browser_factory=lambda: browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
        launch_delay_s=launch_delay_s,
    )
    return orch
```

`_fake_browser()` is a `MagicMock`, so `browser.headless` would otherwise be a truthy mock — setting it explicitly is what makes these tests meaningful.

```python
@pytest.mark.asyncio
async def test_launch_delay_pauses_a_visible_browser(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("agent.orchestrator.asyncio.sleep", fake_sleep)
    await _delay_orchestrator(launch_delay_s=3.0, headless=False).run_plan("X")
    assert 3.0 in slept


@pytest.mark.asyncio
async def test_launch_delay_skipped_when_headless(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("agent.orchestrator.asyncio.sleep", fake_sleep)
    await _delay_orchestrator(launch_delay_s=3.0, headless=True).run_plan("X")
    assert 3.0 not in slept


@pytest.mark.asyncio
async def test_launch_delay_of_zero_never_sleeps(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("agent.orchestrator.asyncio.sleep", fake_sleep)
    await _delay_orchestrator(launch_delay_s=0.0, headless=False).run_plan("X")
    assert slept == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -k launch_delay -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'launch_delay_s'`

- [ ] **Step 3: Add the constructor parameter**

In `agent/orchestrator.py`, add `launch_delay_s: float | None = None` to the `__init__` signature (`agent/orchestrator.py:41`), then after the `step_attempt_budget_s` line (`agent/orchestrator.py:65`) add:

```python
        # Pause between a case's browser appearing and its first action, so a
        # human watching a visible window can follow along. Pointless when
        # headless, where it would only add wall clock (3s × 73 cases ≈ 3.5min),
        # so _execute_case skips it there.
        self.launch_delay_s = (
            launch_delay_s if launch_delay_s is not None
            else float(os.environ.get("AGENT_LAUNCH_DELAY_S", "3"))
        )
```

Add `import asyncio` to the module's imports (it currently imports `logging`, `os`, `time`).

- [ ] **Step 4: Apply the delay**

In `_execute_case`, immediately after `await browser.open_session()` and **before** `await login(browser)` (`agent/orchestrator.py:176-177`):

```python
                await browser.open_session()
                if self.launch_delay_s > 0 and not getattr(browser, "headless", True):
                    log.info(
                        "Launch delay: %.1fs before the first action of %s",
                        self.launch_delay_s, case_id,
                    )
                    await asyncio.sleep(self.launch_delay_s)
                await login(browser)
```

`getattr(..., True)` defaults to "headless" so a fake session without the attribute never sleeps in tests.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: all pass, including the three new ones.

- [ ] **Step 6: Commit**

```bash
git add agent/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: pause after a visible browser launches, before the first action"
```

---

### Task 4: run_state gains precondition and test data

**Files:**
- Modify: `agent/run_state.py:19-54` (`Step`, `TestCase`)
- Modify: `FRONTEND.md` (contract block near `FRONTEND.md:281-293`)
- Modify: `fixtures/sample_run_state.json`
- Modify: `frontend/public/fixtures/sample_run_state.json`
- Test: `tests/test_run_state.py:70-117`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Step(test_data: str | None = None)`; `TestCase(precondition: str | None = None, test_data: list[dict[str, str]] = [])`. Serialized case keys become `{id, name, status, precondition, test_data, steps}`; step keys gain `test_data`. Tasks 5 and 8 depend on exactly these names.

- [ ] **Step 1: Write the failing tests**

In `tests/test_run_state.py`, update the two key-set assertions and add coverage. Replace the `set(case_d.keys())` assertion at line 70 with:

```python
    assert set(case_d.keys()) == {
        "id",
        "name",
        "status",
        "precondition",
        "test_data",
        "steps",
    }
```

Extend the step key set at line 72 to include `"test_data"`. Then append:

```python
def test_case_carries_precondition_and_test_data():
    state = new_run_state("X")
    state.add_case(
        TestCase(
            id="SOUSCLOUD-TC-1985",
            name="Edit inventory",
            precondition="User is signed in as Admin",
            test_data=[{"name": "User Role", "value": "Admin"}],
        )
    )
    case_d = state.to_dict()["test_cases"][0]
    assert case_d["precondition"] == "User is signed in as Admin"
    assert case_d["test_data"] == [{"name": "User Role", "value": "Admin"}]


def test_case_defaults_precondition_null_and_test_data_empty():
    state = new_run_state("X")
    state.add_case(TestCase(id="TC-1", name="No context"))
    case_d = state.to_dict()["test_cases"][0]
    assert case_d["precondition"] is None
    assert case_d["test_data"] == []


def test_step_carries_its_own_test_data():
    state = new_run_state("X")
    state.add_case(TestCase(id="TC-1", name="c"))
    state.add_step("TC-1", Step(action="Type the name", detail="…", test_data="Recipe A"))
    step_d = state.to_dict()["test_cases"][0]["steps"][0]
    assert step_d["test_data"] == "Recipe A"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_run_state.py -v`
Expected: FAIL — unexpected keyword `precondition`, and the key-set assertions mismatch.

- [ ] **Step 3: Add the fields**

In `agent/run_state.py`, add to `Step` after `screenshot_b64` (line 26):

```python
    test_data: str | None = None
```

and to its `to_dict()`:

```python
            "test_data": self.test_data,
```

Add to `TestCase` after `status` (line 43):

```python
    precondition: str | None = None
    test_data: list[dict[str, str]] = field(default_factory=list)
```

Note `steps` must stay the last field with a default; `field` is already imported. Add to `TestCase.to_dict()`, keeping `steps` last:

```python
            "precondition": self.precondition,
            "test_data": list(self.test_data),
```

- [ ] **Step 4: Update both fixtures**

In `fixtures/sample_run_state.json` and `frontend/public/fixtures/sample_run_state.json`, add `"precondition"` and `"test_data"` to every case object and `"test_data"` to every step object. The two files must stay byte-identical in shape — `test_run_state.py` asserts fixture parity and Vite serves the public copy in dev. Use realistic values on the first case so the UI has something to render:

```json
      "precondition": "Tester is signed in as Admin and on the Dashboard",
      "test_data": [
        { "name": "User Role", "value": "Admin" },
        { "name": "Menu", "value": "Recipe" }
      ],
```

and on one step `"test_data": "Recipe A"`, with `"test_data": null` on the rest.

- [ ] **Step 5: Update FRONTEND.md**

In the run_state contract block (around `FRONTEND.md:281-293`), add the fields with comments matching the existing style:

```json
  "test_cases": [
    {
      "id": "IRHS-R-01",
      "name": "Create a recipe",
      "status": "pass",
      "precondition": "…",        // QMetry precondition, or null
      "test_data": [              // case-level QMetry parameter table; [] when none
        { "name": "User Role", "value": "Admin" }
      ],
      "steps": [
        {
          "action": "…",
          "detail": "…",
          "status": "pass",
          "evaluation": "…",
          "duration_seconds": 1.4,
          "screenshot_b64": null, // base64 PNG of the page after the step, or null
          "test_data": null       // QMetry per-step testData, or null
        }
      ]
    }
  ]
```

Add a sentence stating that the live tape renders precondition → case test data → steps, matching the Manual panel, and that a step with no test data shows an italic *none*.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_run_state.py -v`
Expected: all pass, fixture parity included.

- [ ] **Step 7: Commit**

```bash
git add agent/run_state.py tests/test_run_state.py FRONTEND.md fixtures/sample_run_state.json frontend/public/fixtures/sample_run_state.json
git commit -m "feat: add precondition and test data to the run_state contract"
```

---

### Task 5: Orchestrator fills the new fields

**Files:**
- Modify: `agent/orchestrator.py:85-86` (`run_plan` pre-populate loop), `agent/orchestrator.py:132` (`run_single_case`), `agent/orchestrator.py:284` (`Step` construction)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `TestCase(precondition=…, test_data=…)` and `Step(test_data=…)` from Task 4.
- Produces: no new signatures. Case dicts from any `CaseSource` may carry `precondition: str` and `test_data: list[{name, value}]`; step dicts may carry `test_data: str`. All are optional — `FixtureCaseSource` cases without them still work.

**Behavior change worth knowing:** `_execute_step` currently glues test data onto the action text (`action_text`) and stores *that* on the tape. The model must keep receiving the joined text, but the tape should now show the raw action with test data as its own labelled field — which is exactly what the Manual panel does.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`. Add one shared builder beside the existing fakes:

```python
def _passing_orchestrator(cases: list[dict]):
    """Orchestrator over `cases` where every step translates and passes."""
    n = sum(len(c["steps"]) for c in cases)
    return Orchestrator(
        azure=_fake_azure(
            translate_side_effect=[_ok_actions() for _ in range(n)],
            evaluate_side_effect=[{"status": "pass", "reason": "ok"} for _ in range(n)],
        ),
        browser_factory=_fake_browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )
```

```python
@pytest.mark.asyncio
async def test_run_plan_copies_precondition_and_case_test_data():
    cases = [{
        "id": "TC-1",
        "name": "Edit inventory",
        "precondition": "Signed in as Admin",
        "test_data": [{"name": "Menu", "value": "Recipe"}],
        "steps": [{"action": "Open the dashboard", "expected": "Dashboard loads"}],
    }]
    state = await _passing_orchestrator(cases).run_plan("X")
    d = state.to_dict()["test_cases"][0]
    assert d["precondition"] == "Signed in as Admin"
    assert d["test_data"] == [{"name": "Menu", "value": "Recipe"}]


@pytest.mark.asyncio
async def test_step_test_data_is_separate_from_the_action_on_the_tape():
    cases = [{
        "id": "TC-1",
        "name": "Create recipe",
        "steps": [{
            "action": "Type the recipe name",
            "expected": "Name accepted",
            "test_data": "Recipe A",
        }],
    }]
    state = await _passing_orchestrator(cases).run_plan("X")
    step_d = state.to_dict()["test_cases"][0]["steps"][0]
    assert step_d["test_data"] == "Recipe A"
    assert step_d["action"] == "Type the recipe name"
    assert "Test data:" not in step_d["action"]


@pytest.mark.asyncio
async def test_cases_without_the_new_fields_still_run():
    cases = [{
        "id": "TC-1",
        "name": "Bare case",
        "steps": [{"action": "Open the dashboard", "expected": "Dashboard loads"}],
    }]
    state = await _passing_orchestrator(cases).run_plan("X")
    d = state.to_dict()["test_cases"][0]
    assert d["precondition"] is None
    assert d["test_data"] == []
    assert d["steps"][0]["test_data"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -k "precondition or test_data" -v`
Expected: FAIL — `precondition` is `None` and `test_data` is `[]`/absent because nothing populates them.

- [ ] **Step 3: Populate the case fields**

Replace the pre-populate loop in `run_plan` (`agent/orchestrator.py:85-86`) with:

```python
        # Pre-populate the rail so the tester can see what's coming. Precondition
        # and case test data ride along from the case list — no extra QMetry call.
        for c in cases:
            state.add_case(
                TestCase(
                    id=c["id"],
                    name=c["name"],
                    precondition=c.get("precondition") or None,
                    test_data=list(c.get("test_data") or []),
                )
            )
```

Replace the `state.add_case(...)` line in `run_single_case` (`agent/orchestrator.py:132`) with:

```python
        state.add_case(
            TestCase(
                id=match["id"],
                name=match["name"],
                precondition=match.get("precondition") or None,
                test_data=list(match.get("test_data") or []),
            )
        )
```

- [ ] **Step 4: Separate step test data from the action**

Replace the `rs_step` construction (`agent/orchestrator.py:284`) with:

```python
        # The model gets action + test data joined (action_text); the tape keeps
        # them apart so the console can label test data per step, exactly as the
        # Manual panel does.
        rs_step = Step(
            action=step["action"],
            detail="translating…",
            status="running",
            test_data=step.get("test_data") or None,
        )
```

Leave `action_text` and everything downstream of it untouched — the prompt must not change.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: all pass. If an older test asserted the tape's `action` contained `"Test data:"`, update it — that expectation is what this task deliberately changes.

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add agent/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: carry precondition and test data into the live run tape"
```

---

### Task 6: run_plan accepts credentials

**Files:**
- Modify: `agent/orchestrator.py:69-101` (`run_plan`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `_execute_case(..., credentials=…)`, which already exists (`agent/orchestrator.py:153`).
- Produces:

```python
async def run_plan(
    self,
    plan_key: str,
    credentials: tuple[str, str] | None = None,
    case_credentials: dict[str, tuple[str, str]] | None = None,
) -> RunState
```

Per case: `case_credentials.get(case_id)` wins, else `credentials`, else `None` (meaning the `.env` account). Task 7 calls this. `ManualStore` is deliberately not imported here — the server builds the map.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

Add these two helpers beside the existing fakes:

```python
def _bare_case(case_id: str) -> dict:
    return {
        "id": case_id,
        "name": f"case {case_id}",
        "steps": [{"action": "Click go", "expected": "Page loaded"}],
    }


def _credential_recorder(cases: list[dict]) -> tuple[Orchestrator, list]:
    """Orchestrator whose _execute_case only records the credentials it got."""
    seen: list[tuple[str, str] | None] = []
    orch = Orchestrator(
        azure=_fake_azure(),
        browser_factory=_fake_browser,
        case_source=FakeCaseSource({"key": "X", "name": "x"}, cases),
        on_update=lambda s: None,
    )

    async def _fake(state, case, dry_run=False, step_indices=None, credentials=None):
        seen.append(credentials)
        state.resolve_case(case["id"], "pass")

    orch._execute_case = _fake
    return orch, seen
```

```python
@pytest.mark.asyncio
async def test_run_plan_passes_run_level_credentials_to_every_case():
    orch, seen = _credential_recorder([_bare_case("TC-1"), _bare_case("TC-2")])
    await orch.run_plan("X", credentials=("qa@duke", "pw"))
    assert seen == [("qa@duke", "pw"), ("qa@duke", "pw")]


@pytest.mark.asyncio
async def test_per_case_credentials_override_the_run_level_pair():
    orch, seen = _credential_recorder([_bare_case("TC-1"), _bare_case("TC-2")])
    await orch.run_plan(
        "X",
        credentials=("run@duke", "pw"),
        case_credentials={"TC-2": ("special@duke", "other")},
    )
    assert seen == [("run@duke", "pw"), ("special@duke", "other")]


@pytest.mark.asyncio
async def test_no_credentials_means_none_so_login_uses_dotenv():
    orch, seen = _credential_recorder([_bare_case("TC-1")])
    await orch.run_plan("X")
    assert seen == [None]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -k credentials -v`
Expected: FAIL — `run_plan() got an unexpected keyword argument 'credentials'`

- [ ] **Step 3: Write the implementation**

Change the `run_plan` signature and docstring (`agent/orchestrator.py:69-70`):

```python
    async def run_plan(
        self,
        plan_key: str,
        credentials: tuple[str, str] | None = None,
        case_credentials: dict[str, tuple[str, str]] | None = None,
    ) -> RunState:
        """Run an entire plan end-to-end. Returns the final RunState.

        `credentials` is the run-level (username, password) override; a case id
        present in `case_credentials` uses that pair instead. None means the
        .env account. Credentials never enter run_state, a prompt, or a log
        line — they reach BrowserSession.credentials and nowhere else. The
        per-case map is built by the caller (server.py reads ManualStore) so
        this module stays independent of the manual session store.
        """
```

Replace the case loop (`agent/orchestrator.py:91-97`):

```python
        per_case = case_credentials or {}
        for case in cases:
            try:
                await self._execute_case(
                    state, case, credentials=per_case.get(case["id"]) or credentials
                )
            except Exception:
                log.exception("Case %s crashed; marking blocked", case.get("id"))
                state.resolve_case(case["id"], "blocked")
                self.on_update(state)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: run_plan accepts run-level and per-case credentials"
```

---

### Task 7: POST /runs takes a login

**Files:**
- Modify: `server.py:74-76` (`StartRunBody`), `server.py:188-201` (`_run_in_background`), `server.py:345-360` (`start_run`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `run_plan(plan_key, credentials=…, case_credentials=…)` from Task 6; `MANUAL` / `ManualStore.get(plan)` and `session.find_case(id).mark.login_username|login_password`, already used at `server.py:216-224`.
- Produces: `POST /runs` body `{plan, username?, password?}` → `{run_id}`. A module-level `RUN_CREDENTIALS: dict[str, tuple[str, str]]` holds the pair for the run's lifetime and is deleted when the run ends. No response, snapshot, or SSE event ever carries it.

- [ ] **Step 1: Write the failing tests**

First extend the autouse `_clear_registries` fixture (`tests/test_server.py:19-30`) to reset the new registry in both the setup and teardown halves, so a leaked credential can never cross tests:

```python
    server_mod.RUN_CREDENTIALS.clear()
```

Then append these tests. They follow the module's real pattern — `client` fixture plus `patch.object(server_mod, "_run_in_background", new=AsyncMock())` to neutralize the background task — and test the forwarding by awaiting `_run_in_background` directly rather than racing a real task:

```python
def test_post_runs_records_credentials_for_the_run(client):
    with patch.object(server_mod, "_run_in_background", new=AsyncMock()):
        r = client.post(
            "/runs",
            json={"plan": "SOUSCLOUD-TR-482", "username": "qa@duke", "password": "pw"},
        )
    run_id = r.json()["run_id"]
    assert server_mod.RUN_CREDENTIALS[run_id] == ("qa@duke", "pw")


def test_post_runs_ignores_a_half_filled_login(client):
    with patch.object(server_mod, "_run_in_background", new=AsyncMock()):
        r = client.post(
            "/runs", json={"plan": "P", "username": "qa@duke", "password": ""}
        )
    assert r.json()["run_id"] not in server_mod.RUN_CREDENTIALS


def test_get_run_never_exposes_credentials(client):
    with patch.object(server_mod, "_run_in_background", new=AsyncMock()):
        r = client.post(
            "/runs", json={"plan": "P", "username": "qa@duke", "password": "s3cret"}
        )
    body = client.get(f"/runs/{r.json()['run_id']}").text
    assert "s3cret" not in body
    assert "qa@duke" not in body


@pytest.mark.asyncio
async def test_run_in_background_forwards_credentials_then_clears_them(monkeypatch):
    captured = {}

    class FakeOrch:
        async def run_plan(self, plan_key, credentials=None, case_credentials=None):
            captured["credentials"] = credentials
            captured["case_credentials"] = case_credentials
            return new_run_state(plan_key)

    monkeypatch.setattr(server_mod, "_build_orchestrator", lambda on_update: FakeOrch())
    monkeypatch.setattr(server_mod, "_manual_case_credentials", lambda plan: {})
    state = new_run_state("P")
    server_mod.RUN_CREDENTIALS[state.run_id] = ("qa@duke", "pw")

    await server_mod._run_in_background(state.run_id, "P", state)

    assert captured["credentials"] == ("qa@duke", "pw")
    assert state.run_id not in server_mod.RUN_CREDENTIALS


def test_manual_case_credentials_collects_only_complete_logins(monkeypatch):
    class _Mark:
        def __init__(self, user, pw):
            self.login_username = user
            self.login_password = pw

    class _Case:
        def __init__(self, case_id, mark):
            self.id = case_id
            self.mark = mark

    class _Session:
        cases = [
            _Case("TC-2", _Mark("a@duke", "pw")),
            _Case("TC-3", _Mark("b@duke", "")),      # no password — skipped
            _Case("TC-4", _Mark("", "")),            # nothing saved — skipped
        ]

    monkeypatch.setattr(server_mod.MANUAL, "get", lambda plan: _Session())
    assert server_mod._manual_case_credentials("P") == {"TC-2": ("a@duke", "pw")}


def test_manual_case_credentials_empty_when_no_session(monkeypatch):
    monkeypatch.setattr(server_mod.MANUAL, "get", lambda plan: None)
    assert server_mod._manual_case_credentials("P") == {}
```

`new_run_state`, `patch`, `AsyncMock`, and `pytest` are already imported by this module.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -k credential -v`
Expected: FAIL — `StartRunBody` rejects `username`/`password`, or `credentials` arrives as `None`.

- [ ] **Step 3: Extend the request model**

Replace `StartRunBody` (`server.py:74-76`):

```python
class StartRunBody(BaseModel):
    plan: str
    # Run-level login override. Inbound only: held in memory for the run and
    # never echoed in a response, snapshot, or SSE event. Both blank = .env admin.
    username: str = ""
    password: str = ""
```

- [ ] **Step 4: Hold the pair and forward it**

Add beside the other module-level run registries (near `RUNS` / `LATEST` / `TASKS`):

```python
# run_id -> (username, password) for the lifetime of that run only. Never
# serialized; deleted when the run ends.
RUN_CREDENTIALS: dict[str, tuple[str, str]] = {}
```

Replace `_run_in_background` (`server.py:188-201`):

```python
async def _run_in_background(run_id: str, plan_key: str, state: RunState) -> None:
    """Wrap orch.run_plan so exceptions don't crash the task silently."""
    try:
        orch = _build_orchestrator(_make_on_update(run_id))
        # The orchestrator builds its own RunState. We want it to write into the
        # already-registered state object so RUNS[run_id] stays the same ref.
        # Easiest: have the orchestrator return a fresh state and replace RUNS[run_id].
        final = await orch.run_plan(
            plan_key,
            credentials=RUN_CREDENTIALS.get(run_id),
            case_credentials=_manual_case_credentials(plan_key),
        )
        RUNS[run_id] = final
    except Exception:
        log.exception("Run %s crashed", run_id)
        # mark blocked so the UI shows something terminal
        state.finish()
        _make_on_update(run_id)(state)
    finally:
        RUN_CREDENTIALS.pop(run_id, None)


def _manual_case_credentials(plan_key: str) -> dict[str, tuple[str, str]]:
    """Per-case logins saved in the Manual tab, which outrank the run-level pair.

    Kept here rather than in the orchestrator so agent/ never imports the
    manual session store.
    """
    session = MANUAL.get(plan_key)
    if session is None:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for case in session.cases:
        mark = case.mark
        if mark.login_username and mark.login_password:
            out[case.id] = (mark.login_username, mark.login_password)
    return out
```

`ManualSession.cases` is a `list[ManualCase]` and each entry exposes `.id` and `.mark` (`agent/manual_state.py:137`, `agent/manual_state.py:168-169`), so the loop above is correct as written. This mirrors the existing per-case lookup in `_run_agent_case` (`server.py:216-224`), which reads the same two mark fields.

- [ ] **Step 5: Register the credentials at run start**

In `start_run` (`server.py:345-360`), after `LISTENERS.setdefault(...)` and before creating the task:

```python
    if body.username and body.password:
        RUN_CREDENTIALS[state.run_id] = (body.username, body.password)
```

Both must be non-empty — a username with no password falls back to the `.env` account rather than half-attempting a login.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -v`
Expected: all pass.

- [ ] **Step 7: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: green.

- [ ] **Step 8: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: POST /runs accepts a run-level login, Manual per-case wins"
```

---

### Task 8: Live console — login fields, precondition, test data

**Files:**
- Modify: `frontend/src/hooks/useRunState.js:46-55` (`startRun`)
- Modify: `frontend/src/App.jsx:171-187` (`handleRun`), `frontend/src/App.jsx:246-265` (stage head + tape)
- Modify: `frontend/src/components/ExecutionTape.jsx`
- Modify: `frontend/src/components/Step.jsx`
- Modify: `frontend/src/tokens.css`
- Modify: `FRONTEND.md`

**Interfaces:**
- Consumes: `startRun(planKey, credentials)`; the run_state fields from Task 4 (`case.precondition`, `case.test_data`, `step.test_data`); `POST /runs` body from Task 7.
- Produces: no exports beyond the changed `startRun` signature.

**Reuse, don't reinvent:** the Manual panel already renders all of this. Copy the markup and class names from `frontend/src/components/ManualCase.jsx:205-231` (credentials row and helper copy) and its precondition/test-data blocks, so the two views look identical.

- [ ] **Step 1: Send credentials from the hook**

Replace `startRun` (`frontend/src/hooks/useRunState.js:47-55`):

```js
// Convenience: kicks off a real run. Returns { run_id }.
// `credentials` is an optional { username, password }; both must be non-empty
// to be sent at all, otherwise the backend uses the .env admin account.
export async function startRun(planKey, credentials) {
  const body = { plan: planKey }
  if (credentials?.username && credentials?.password) {
    body.username = credentials.username
    body.password = credentials.password
  }
  const res = await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Failed to start run: ${res.status}`)
  return res.json()
}
```

- [ ] **Step 2: Add the login fields beside Run plan**

In `frontend/src/App.jsx`, add state near the other live-run state (`useState` is already imported at `frontend/src/App.jsx:7`):

```jsx
  const [runUser, setRunUser] = useState('')
  const [runPw, setRunPw] = useState('')
```

Pass them in `handleRun` (`frontend/src/App.jsx:176`):

```jsx
      const { run_id } = await startRun(planKey, { username: runUser, password: runPw })
```

In the stage head beside the Run button (`frontend/src/App.jsx:256-262`), add the row. Class names match the Manual panel so it inherits the same styling:

```jsx
            <div className="run-credentials">
              <span className="manual-credentials-label">Login as</span>
              <input
                type="text"
                className="manual-credentials-input mono"
                placeholder="username (default admin)"
                value={runUser}
                onChange={(e) => setRunUser(e.target.value)}
                disabled={isRunning}
                aria-label="Run login username"
              />
              <input
                type="password"
                className="manual-credentials-input mono"
                placeholder="password"
                value={runPw}
                onChange={(e) => setRunPw(e.target.value)}
                disabled={isRunning}
                aria-label="Run login password"
              />
            </div>
            <p className="manual-credentials-help">
              Leave blank to use the system admin account. A case with its own login
              saved on the Manual tab uses that instead.
            </p>
```

- [ ] **Step 3: Show precondition and case test data in the tape**

In `frontend/src/components/ExecutionTape.jsx`, render the case context above the steps, inside `tape-wrap` and before the `tape-section-label`:

```jsx
      {activeCase?.precondition && (
        <div className="tape-precondition">
          <span className="tape-context-label">Precondition</span>
          <p>{activeCase.precondition}</p>
        </div>
      )}
      {activeCase?.test_data?.length > 0 && (
        <div className="tape-test-data">
          <span className="tape-context-label">Test data</span>
          <dl>
            {activeCase.test_data.map((d) => (
              <div key={d.name}>
                <dt className="mono">{d.name}</dt>
                <dd className="mono">{d.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
```

- [ ] **Step 4: Show per-step test data**

In `frontend/src/components/Step.jsx`, destructure `test_data` (line 7) and render it under `step-detail`, above the evaluation:

```jsx
        <div className="step-test-data">
          <span className="step-test-data-label">Test data</span>{' '}
          {test_data ? (
            <span className="mono">{test_data}</span>
          ) : (
            <em>none</em>
          )}
        </div>
```

The italic *none* is deliberate and matches the Manual panel — do not hide the row when empty.

- [ ] **Step 5: Style the new blocks**

Add to `frontend/src/tokens.css`. These use the file's real tokens — `--line` for rules, `--muted`/`--faint` for de-emphasized ink, `--navy-soft` for tinted panels. The file has **no** `--space-*` scale, so spacing is literal px, matching the existing rules:

```css
.run-credentials { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.tape-precondition,
.tape-test-data { padding: 12px 18px; border-bottom: 1px solid var(--line); }
.tape-test-data { background: var(--navy-soft); }

.tape-context-label {
  display: block;
  font-size: 0.6875rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--faint);
  margin-bottom: 4px;
}
.tape-precondition p { margin: 0; color: var(--ink); }

.tape-test-data dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 16px;
  margin: 0;
}
.tape-test-data dt { color: var(--muted); }
.tape-test-data dd { margin: 0; color: var(--ink); }

.step-test-data { font-size: 0.8125rem; color: var(--muted); margin-top: 4px; }
.step-test-data-label {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.6875rem;
  color: var(--faint);
}
```

- [ ] **Step 6: Build and verify by hand**

Run: `cd frontend && npm run build`
Expected: build succeeds with no unresolved imports.

Then restart the server and check in the browser:

```powershell
.venv\Scripts\python.exe server.py
```

- Open a run, confirm "Login as" appears beside **Run plan** and both inputs disable while running.
- Open a case with known parameters (`SOUSCLOUD-TC-1985`) and confirm precondition and the test-data table render above the tape.
- Start a run and confirm each step shows its test data or an italic *none*, and that the step screenshot now carries the URL strip at the top.
- Confirm the network tab shows `password` on the `POST /runs` request and **never** in any `GET /runs/{id}` response.

- [ ] **Step 7: Update FRONTEND.md**

Document the live-run login row (placement, disabled-during-run, helper copy, precedence note) and the tape's precondition / test-data blocks in the Live-run section, matching how the Manual panel's equivalents are already described.

- [ ] **Step 8: Commit**

```bash
git add frontend/src FRONTEND.md
git commit -m "feat: live run gets login fields, precondition and test data"
```

---

## Final verification

- [ ] Run the whole suite: `.venv\Scripts\python.exe -m pytest tests/ -q` — expect green (284 at plan time, ~305 after).
- [ ] Run one live case end to end with `HEADLESS=false` and confirm: the window appears, pauses ~3s, then logs in; screenshots carry the URL strip; the tape shows precondition and test data.
- [ ] Set `AGENT_LAUNCH_DELAY_S=0` and confirm the pause disappears.
- [ ] Confirm `manual_sessions/` still holds the only persisted passwords — `git status` must show no new file containing a credential.
