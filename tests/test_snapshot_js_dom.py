"""Real-Chromium tests for the hidden-elements DOM heuristic in `_SNAPSHOT_JS`.

Every other test in this suite runs against a MOCKED Playwright `Page` -- that
is this project's convention, and it is fast and reliable. This file breaks
that convention on purpose.

The hidden-children pass in `agent.browser._SNAPSHOT_JS` (the DOM heuristic
that finds interactive elements hidden inside a collapsed submenu or a
pre-rendered modal, and names the visible control that reveals each one) is a
pure DOM walk: `getBoundingClientRect`, `getComputedStyle`,
`previousElementSibling`, `parentElement`. A mock cannot execute it, so a
mocked-`Page` test can only assert what Python does with a canned return
value -- it can never catch a bug IN the JS itself.

That is not hypothetical. Two independent, careful reviews of this exact
block each introduced (round 1) and then missed (round 2) a bug that made
the JS heuristic behave wrong -- and the second one, a stray `seen.has(el) ||`
guard, made the entire hidden pass dead code: it could never emit anything,
in every environment, and the mocked-`Page` suite stayed green throughout.
Only a real DOM can catch that class of bug, so this file drives a real
headless Chromium against small `set_content()` fixtures -- no network, no
app, no server -- for exactly the DOM shapes those two reviews discussed.

If a shape below fails, that is the heuristic being wrong against a real
browser; do not weaken the assertion to match the code.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")

import pytest_asyncio  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from agent.browser import BrowserSession, MAX_HIDDEN_ELEMENTS, MAX_SNAPSHOT_ELEMENTS  # noqa: E402

# Every test in this module shares one module-scoped event loop (required so
# the module-scoped `browser` fixture below, and the Chromium connection it
# holds, can be reused across tests instead of launching a fresh browser per
# test).
pytestmark = pytest.mark.asyncio(loop_scope="module")


# --------------------------------------------------------------------------
# Module-scoped browser: launch once, skip cleanly (never fail) if Playwright
# or the chromium browser binary is unavailable on this machine -- e.g. no
# `playwright install chromium` has been run. This file must never turn a
# missing local install into a failing suite.
# --------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser():
    try:
        pw = await async_playwright().start()
        b = await pw.chromium.launch(headless=True)
    except Exception as e:  # missing browser binary, no display server, etc.
        pytest.skip(f"chromium unavailable - skipping real-DOM tests: {e}")
        return
    yield b
    await b.close()
    await pw.stop()


async def _snapshot_for(browser, html: str) -> list[dict]:
    """Load `html` in a fresh page (no navigation, no network) and return
    what snapshot_elements() finds."""
    page = await browser.new_page()
    try:
        await page.set_content(html)
        session = BrowserSession(headless=True, base_url="https://example.invalid")
        session._page = page
        return await session.snapshot_elements()
    finally:
        await page.close()


def _hidden(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("hidden")]


def _by_name(entries: list[dict], name: str) -> dict | None:
    return next((e for e in entries if e.get("name") == name), None)


def _ref_of(entries: list[dict], name: str) -> str | None:
    e = next((e for e in entries if e.get("name") == name and e.get("ref")), None)
    return e["ref"] if e else None


# --------------------------------------------------------------------------
# Shapes
# --------------------------------------------------------------------------


async def test_nav_submenu_canonical(browser):
    """A collapsed submenu (display:none) whose only preceding visible
    control is its own toggle anchor."""
    html = """
    <nav>
      <li><a href="#">Recipe</a>
        <ul style="display:none">
          <li><a href="/inv">Edit Inventory</a></li>
        </ul>
      </li>
    </nav>
    """
    out = await _snapshot_for(browser, html)
    hidden = _hidden(out)
    entry = _by_name(hidden, "Edit Inventory")
    assert entry is not None, f"expected an 'Edit Inventory' hidden entry, got {out}"
    recipe_ref = _ref_of(out, "Recipe")
    assert recipe_ref is not None
    assert entry["parent_ref"] == recipe_ref
    assert entry["parent"] == "Recipe"


async def test_toggle_is_nearest_of_several_siblings(browser):
    """Regression test for round 1 finding 2: the toggle must be the LAST
    (nearest) tagged sibling, not the first one in document order."""
    html = """
    <nav>
      <div class="group">
        <a href="#">Dashboard</a>
        <a href="#">Equipment</a>
        <a href="#">Recipe</a>
      </div>
      <ul style="display:none">
        <li><a href="/inv">Edit Inventory</a></li>
      </ul>
    </nav>
    """
    out = await _snapshot_for(browser, html)
    hidden = _hidden(out)
    entry = _by_name(hidden, "Edit Inventory")
    assert entry is not None, f"expected an 'Edit Inventory' hidden entry, got {out}"
    assert entry["parent"] == "Recipe", (
        f"toggle resolved to {entry['parent']!r}, expected 'Recipe' "
        "(nearest sibling) not 'Dashboard' (farthest)"
    )


async def test_body_level_modal_is_skipped(browser):
    """Regression test for round 1 finding 3: a container whose parent is
    <body> has no toggle and must not be emitted at all."""
    html = """
    <header><a href="#">Dashboard</a></header>
    <main>x</main>
    <div class="modal" style="display:none">
      <button>Confirm Delete</button>
    </div>
    """
    out = await _snapshot_for(browser, html)
    hidden = _hidden(out)
    entry = _by_name(hidden, "Confirm Delete")
    assert entry is None, f"body-level modal control should be skipped, got {entry}"


async def test_element_hidden_by_its_own_style(browser):
    """Regression test for round 1 finding 6: an element hidden directly
    (legacy jQuery .hide()) rather than via a wrapping container."""
    html = """
    <nav>
      <li>
        <a href="#">Recipe</a>
        <a href="/inv" style="display:none">Edit Inventory</a>
      </li>
    </nav>
    """
    out = await _snapshot_for(browser, html)
    hidden = _hidden(out)
    entry = _by_name(hidden, "Edit Inventory")
    assert entry is not None, f"expected an 'Edit Inventory' hidden entry, got {out}"
    assert entry["parent"] == "Recipe"


async def test_deeply_nested_hiding_container(browser):
    """Regression test for round 2 finding 2: the hider walk must be
    unbounded (not capped at depth 2), or a submenu nested three levels
    deep inside its `display:none` container is silently dropped."""
    html = """
    <nav>
      <a href="#">Recipe</a>
      <div class="submenu" style="display:none">
        <ul>
          <li><a href="/inv">Edit Inventory</a></li>
        </ul>
      </div>
    </nav>
    """
    out = await _snapshot_for(browser, html)
    hidden = _hidden(out)
    entry = _by_name(hidden, "Edit Inventory")
    assert entry is not None, f"expected an 'Edit Inventory' hidden entry, got {out}"
    assert entry["parent"] == "Recipe"


async def test_name_whitespace_is_normalised(browser):
    """Regression test for round 1 finding 4: descendant icon/badge markup
    inside a hidden anchor must not leak a newline or run of whitespace into
    `name`."""
    html = """
    <nav>
      <a href="#">Recipe</a>
      <a href="/inv" style="display:none">
        <i class="fa fa-pencil"></i>
        Edit Inventory
        <span class="badge">3</span>
      </a>
    </nav>
    """
    out = await _snapshot_for(browser, html)
    hidden = _hidden(out)
    assert len(hidden) == 1, f"expected exactly one hidden entry, got {out}"
    name = hidden[0]["name"]
    assert "\n" not in name
    assert "\t" not in name
    assert "  " not in name  # no doubled spaces from collapsed whitespace
    assert name == "Edit Inventory 3"


async def test_hidden_pass_is_not_dead_code(browser):
    """THE test that would have caught round 2's Critical finding: the
    hidden pass silently returning [] in every environment, because a
    `seen.has(el) ||` guard made every candidate already excluded before the
    pass could ever consider it. If this assertion ever goes red again
    without a corresponding code change, the hidden-elements feature has
    gone inert again -- do not delete or weaken it."""
    html = """
    <nav>
      <li><a href="#">Recipe</a>
        <ul style="display:none">
          <li><a href="/inv">Edit Inventory</a></li>
        </ul>
      </li>
    </nav>
    """
    out = await _snapshot_for(browser, html)
    assert len(_hidden(out)) > 0, "hidden pass returned nothing - it is dead code again"


async def test_hidden_pass_survives_a_full_visible_cap(browser):
    """Regression test for the critical finding: hitting the visible cap
    (MAX_SNAPSHOT_ELEMENTS) used to `return` out of the WHOLE function, so
    the hidden-children pass never ran at all on any page with that many
    visible controls -- exactly the shape of a real Users/Equipment/Recipes
    list page (sidebar anchors + N rows of pencil/trash/checkbox controls).

    Builds a page with well over MAX_SNAPSHOT_ELEMENTS visible links AND a
    collapsed nav submenu, and asserts the hidden entry still comes back.

    The filler is deliberately `a[href]` (not `<button>`): `_SNAPSHOT_JS`
    walks selectors in document order WITHIN each selector group, and
    `button` is scanned before `a[href]` in `sels` -- so a page of buttons
    would exhaust the cap before ever reaching the Recipe anchor, and the
    hidden entry's toggle (which must itself be a TAGGED visible element)
    could never resolve. Using links, with Recipe first in document order,
    guarantees Recipe is tagged before the cap is hit.
    """
    fillers = "".join(f'<a href="#">Row {i}</a>' for i in range(MAX_SNAPSHOT_ELEMENTS + 20))
    html = f"""
    <nav>
      <li><a href="#">Recipe</a>
        <ul style="display:none">
          <li><a href="/inv">Edit Inventory</a></li>
        </ul>
      </li>
    </nav>
    {fillers}
    """
    out = await _snapshot_for(browser, html)
    visible = [e for e in out if e.get("ref")]
    assert len(visible) == MAX_SNAPSHOT_ELEMENTS, (
        f"expected the visible pass to stop exactly at the cap, got {len(visible)}"
    )
    hidden = _hidden(out)
    entry = _by_name(hidden, "Edit Inventory")
    assert entry is not None, (
        "hidden pass produced nothing once the visible cap was hit -- "
        "the fall-through regressed"
    )


async def test_caps_are_forwarded_to_a_real_page(browser):
    """Sanity check that MAX_SNAPSHOT_ELEMENTS / MAX_HIDDEN_ELEMENTS reach
    the real page.evaluate() call without raising -- a scalar-vs-object
    mismatch (round 1 finding 7) would blow up destructuring in a real
    browser even though a mock would happily accept anything."""
    html = "<button>Only Button</button>"
    out = await _snapshot_for(browser, html)
    assert isinstance(out, list)
    assert len(out) <= MAX_SNAPSHOT_ELEMENTS + MAX_HIDDEN_ELEMENTS
