# Same-page re-observe + hidden-child discovery — design

**Date:** 2026-09-01
**Status:** approved in brainstorming, awaiting spec review
**Problem owner:** TC-1985 (`Edit Inventory: Admin`) has never reached its
target page.

---

## The failure

TC-1985's steps navigate to `Recipe → Edit Inventory` in the left nav. Live
runs show the agent clicking `Help`, `API Key`, `Recipe`, `Recipe` and never
arriving. It was recorded on 2026-08-27 as "probably the top cause of real
failures now".

Two independent causes, both required for the fix.

### Cause 1 — the loop never re-observes a same-page change

`Orchestrator._attempt_step`'s act→observe loop ends a round only on a URL
change (`agent/orchestrator.py`, end of the round body):

```python
if not navigated:
    break  # whole plan ran on one page — step complete (fast path)
```

Clicking `Recipe` expands a submenu **without navigating**, so `navigated` is
False and the loop exits straight to evaluation. The model is never called
again, so it never receives the fresh snapshot that would contain
`Edit Inventory`.

The condition is doing two jobs at once: "the page did not change" and "a
single-page plan is complete". Those are different facts, and conflating them
is the bug.

### Cause 2 — the model cannot know a hidden child exists

`snapshot_elements()` returns only **visible** elements, so `Edit Inventory`
is absent from PAGE ELEMENTS entirely. Meanwhile
`prompts/step_translator.txt` says:

> Never click a parent menu/tab to "reveal" an element that is already listed

and permits expanding collapsible parents only for "verify the sidebar menus"
style steps. So for TC-1985 the model cannot see the target, has no sanctioned
route to reveal it, and guesses among the visible top-level items — exactly
the observed trace.

Fixing either cause alone changes nothing: re-observe with no discovery gives
the model no reason to click `Recipe` in round 1, and discovery with no
re-observe means it clicks `Recipe` and is never asked again.

---

## Scope decisions taken in brainstorming

| Decision | Choice | Rejected alternative |
|---|---|---|
| Breadth | Fix the general "same-page change" class, not just nav submenus | A nav-only special case — modals and accordions would stay broken |
| Re-observe trigger | **New element names appeared** since the round's own snapshot | Any snapshot difference (nearly every step pays an extra call) |
| Discovery | Snapshot reports hidden children, read from the live DOM | A static hardcoded nav map that silently rots |
| Kill-switch | **None** — ship it on | A flag defaulting to on is a flag nobody turns off |
| Verification | Unit tests + one live TC-1985 run | A regression cycle run (declined; exposure accepted, see Risks) |

---

## 1. The loop change — `agent/orchestrator.py`

Split the two facts the current condition conflates. After the inner action
loop, when nothing navigated:

1. Take a fresh snapshot.
2. Compare it against the snapshot this round was planned from, by
   `(tag, name)` pairs.
3. If names are present that were not there before, the page revealed new UI —
   **continue** the loop with the fresh snapshot.
4. Otherwise **break**, exactly as today.

`(tag, name)` rather than `ref`: refs are assigned per snapshot and renumber
every time, so comparing refs would report every element as new on every
round.

### What does not change

- **The inner action loop is untouched.** Navigation remains the only mid-plan
  break. A plan of `[click Recipe, click Edit Inventory]` cannot occur — the
  model can only target refs that existed in the snapshot it was given, and
  `Edit Inventory` had none.
- **The fast path.** A step whose actions reveal nothing new (fill a field,
  click Save) breaks on the first round as it does today, at no extra cost.
- `max_rounds = 6` and `step_attempt_budget_s` stay as the hard backstops. No
  new ceiling is introduced.

### Termination

Expand-then-collapse converges: clicking `Recipe` a second time collapses the
submenu, which adds no new names, so the loop breaks. A pathological page that
adds a name on every round is bounded by `max_rounds`.

### One evidence fix rides along

Today the final action of a same-page plan deliberately gets no frame — the
settled end-of-step screenshot covers it. Under multi-round that would leave a
submenu-open state uncaptured, which matters because the evaluator judges from
frames. So **when the loop continues on a reveal, capture one frame at that
point.**

Not "capture after every action": a one-action step would then ship two
near-identical frames and halve the useful `EVAL_MAX_FRAMES` window, which is
the largest cost lever in a run.

---

## 2. The snapshot change — `agent/browser.py`

`snapshot_elements()` gains a second pass over interactive elements that are
present in the DOM but **hidden**. For each, walk up to the first ancestor
with `display: none`, then resolve the visible interactive element that
toggles that ancestor. Emit:

```python
{"ref": None, "hidden": True, "parent_ref": "e7", "parent": "Recipe",
 "tag": "a", "role": "", "name": "Edit Inventory"}
```

### Rules

- **No `ref`.** The element is not clickable while hidden, so the model's only
  available move is clicking `parent_ref`. Handing out a ref for something
  `page.click()` would fail on trades one failure mode for another.
- **Unresolvable parent means dropped, not degraded.** An entry with no
  resolvable toggle is omitted entirely. A hint the model cannot act on is
  worse than silence.
- **Separate cap:** `MAX_HIDDEN_ELEMENTS = 20`, collected **after** the visible
  pass, so hidden markup can never starve real elements out of the existing
  60-element budget.
- Entries need a non-empty name.

### Safety at the `_format_detail` boundary

`_attempt_step` builds `seen_elements` with `if _el.get("ref")`, so ref-less
hidden entries cannot enter that map. This matters: `_format_detail` reads it
to name what was clicked, and erasing those names is precisely what caused the
2026-08-27 false-PASS bug. The guard exists already; this design adds a test
asserting it rather than relying on it.

### Rendering — `agent/azure_ai.py`

`translate_step` renders hidden entries as their own block:

```
HIDDEN ELEMENTS (not clickable yet — click the parent ref to reveal):
  under e7 "Recipe":  "Edit Inventory"
```

PAGE ELEMENTS keeps its exact current shape and ordering, so nothing the
prompt already relies on moves.

---

## 3. The prompt change — `prompts/step_translator.txt`

**One additive rule.** If the step's target appears under HIDDEN ELEMENTS,
click its `parent_ref` and expect to be called again with a fresh list.

Deliberately **not** touched:

- the existing "never click a parent to reveal a listed element" rule — still
  correct, it governs elements *already listed*;
- the "Recipe, Help, and Logs are collapsible parents" rule — now redundant
  but harmless.

A broad, unvalidated prompt edit already cost this project one disqualified
evaluator-prompt variant. Minimal diff.

---

## Testing

### Unit (mocked Playwright `Page`, no network)

| Test | Asserts |
|---|---|
| reveal continues the loop | new names in the fresh snapshot → a second translate call happens |
| no reveal keeps the fast path | identical snapshot → exactly one translate call, as today |
| navigation unchanged | a URL change still ends the round and re-observes |
| convergence | expand-then-collapse terminates rather than looping |
| ref-less entries excluded | hidden entries never enter `seen_elements`, so `_format_detail` is unaffected |
| hidden cap holds | >20 hidden elements truncates to 20, visible elements unaffected |
| unresolvable parent dropped | a hidden element with no visible toggle is omitted, not emitted ref-less |
| reveal frame captured | continuing on a reveal appends exactly one frame |

### Live

`main.py --testcase SOUSCLOUD-TC-1985` with `HEADLESS=false`, confirming the
agent reaches `/account/recipes` via `Recipe → Edit Inventory`.

---

## Risks

1. **Parent resolution is a DOM heuristic** and is the one piece mocks cannot
   validate. The live TC-1985 run either confirms it or kills it. If the real
   sidebar markup does not fit the "first `display:none` ancestor" shape, this
   section gets reshaped before anything is committed.
2. **The loop change touches every step of every run.** Live verification
   proves TC-1985 is fixed; it does **not** prove the ~430 currently-passing
   steps are unaffected. A regression cycle run was offered and declined, so
   this exposure is knowingly accepted. `RUN_MODE=stop_on_fail` is currently
   set in `.env`, which sharpens it: a new same-page loop causing one bad step
   ends the whole run.
3. **Expect more failures, correctly.** Per the 2026-08-27 note, now that the
   evaluator can see what was clicked, steps that used to pass on invented
   evidence will start failing. A rise in failures after this change is not
   automatically a regression from it.

## Revert

One commit. The loop change, the snapshot pass, and the prompt rule are
independent edits with no migration and no `run_state` change.

---

## Documentation to update in the same change

- `CLAUDE.md` — `agent/browser.py` (hidden pass + cap) and
  `agent/orchestrator.py` (re-observe condition) module notes.
- No `FRONTEND.md` change: `run_state`'s shape is untouched.
