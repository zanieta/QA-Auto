# TR/TC browser + deferred step loading — design

**Date:** 2026-08-04
**Status:** implemented and live-verified

## Problem

Two complaints, one root cause.

1. The rail's only way in was a `<select>` of the newest 50 test cycles, showing
   bare keys (`SOUSCLOUD-TR-482`) because we believed QMetry exposed no cycle
   name. There was no way to reach a test case that wasn't in a cycle.
2. Opening a run was slow — minutes on a large regression cycle.

The slowness was structural: `QMetryCaseSource.list_cases` made **two QMetry
calls per test case** — one for the name/precondition, one for the steps.

## Live API findings (2026-08-04)

Four things were verified against the live API and changed the design:

| Finding | Consequence |
|---|---|
| `POST /testcycles/search?fields=key,summary` returns a real cycle **name** | Cycles do have names; the old "no cycle name" note was an artifact of not passing `fields`. Corrected in CLAUDE.md. |
| `POST /testcases/search` with `{"filter":{"projectId":…}}` works — 2534 cases, paged | A project-wide TC library browser is possible. |
| `filter.summary` filters **server-side** on both searches (case-insensitive substring) | Search can cover the whole project without loading it. |
| The cycle case search accepts `fields=key,summary,precondition` (`fields=all` does **not**) | The per-case version-detail call disappears entirely. |
| `filter.archived: false` works on cycle search | Archived cycles can be excluded by QMetry, keeping `total` and page contents consistent. |

Human keys (`SOUSCLOUD-TC-2`) work wherever an internal id does.

## Design

### Rail: two-level

**Browse** (`CaseBrowser`) — a `TR`|`TC` segmented toggle, a search box, and one
50-row page with `N of TOTAL` and `Load more`. Search is debounced 300ms and
re-queried server-side; it is *not* a filter over the loaded page. Rows stack the
short key above a two-line-clamped name, because run names share long prefixes
and single-line truncation makes different runs read identically.

**Drilled in** — picking a TR swaps the rail to a back link, the run's key and
real name with a progress bar, and the run's case list. Marking, agent runs and
QMetry push behave exactly as before.

Picking a **TC** opens that one case in the stage and leaves the browser up, so
the tester can walk the list.

### Standalone library cases

A library case is opened as the synthetic plan key `TC:<case key>`, which
`QMetryCaseSource` resolves to a one-case plan with no cycle and no execution id.
This reuses the entire existing Manual machinery — marks, per-step marks,
credentials, agent runs — for free. Because there is no execution to write to,
the session reports `standalone: true`, the console **omits** the push control
(rather than disabling it), and `POST /manual/{plan}/push-qmetry` returns 409.

### Deferred steps

`GET /manual/{plan}` asks the source for `with_steps=False`: one QMetry call for
the whole run, with names and preconditions riding along. Each case carries
`steps_loaded`. Opening a case calls `GET /manual/{plan}/cases/{id}/steps`, which
hydrates just that case and caches it. Until it lands, the stage shows "Loading
steps…" rather than an empty step list, which would let a tester start an agent
run with nothing selected.

Two robustness points, both from problems found while building:

- Steps are cached in a **process-lifetime** `_STEPS_CACHE`, separate from the
  60-second case-list cache, and `ManualStore.build` carries loaded steps across
  session rebuilds. Without both, the open case would empty out every time a
  mark triggered a refresh.
- Paging advances by a server-reported `next_start`, never `items.length`. A page
  can return more rows than it yields (dropped rows), and counting kept rows
  drifts off QMetry's offset and silently skips records.

`Orchestrator.run_single_case` also switched to the cheap list plus a single
hydrate; `run_plan` keeps the eager default since it executes every step.

## Measured result

Same 73-case run (`SOUSCLOUD-TR-434`, 802 steps):

| Path | Time |
|---|---|
| Eager (`with_steps=True`) | 21.1s |
| Deferred (what the console does now) | 2.1s |

**10x**, and the pre-change code was slower still — it made a second call per
case on top of this.

## Contract changes

`FRONTEND.md` updated in the same change:

- `GET /cycles` / `GET /testcases` gain `q`, `start`, `limit` and return
  `{rows…, total, start, limit, next_start}`; cycles now carry `name`.
- `GET /manual/{plan}/cases/{id}/steps` is new.
- Manual session gains `standalone`; each case gains `steps_loaded`.
- Both `sample_manual_state.json` fixtures updated to match.

## Revision, same day — search and chrome

The first cut shipped search as a single pass-through substring. In use that was
wrong in three ways, all fixed:

1. **Word order decided whether search worked.** `"delete recipe"` → 6 results,
   `"recipe delete"` → 0. QMetry can't AND (a list value 400s, and an `and: [...]`
   filter is *silently ignored*, returning the whole project). So the client now
   probes each term's count, scans the pages of the **rarest** term, and ANDs the
   rest locally. Both orders now return the same 31 — and matching words anywhere
   rather than as a phrase is why it's 31 and not 6. If any term matches nothing
   the search returns immediately without scanning.
2. **No key search.** Testers search by key constantly, and a key never appears in
   a name, so `TC-2075` found nothing. Key-shaped queries (`2075`, `TC-2075`,
   `tc 2075`, `SOUSCLOUD-TC-2075`) now expand to the full key and use
   `filter.key`, which QMetry matches only in complete form.
3. **The control was confusing.** Two side-by-side `TR`/`TC` tabs implied a
   comparison; they're alternatives, so it's one `<select>`. `type="search"` drew
   a native glyph that read as a broken dropdown arrow — now `type="text"` with a
   `⌕` icon and a real clear button. `Loading…` no longer appears next to
   `0 of 0`.

Also: `duke-logo.png` shipped with an **opaque black background** (99% opaque
black pixels), so the rail's white brand tile rendered as a black box. The alpha
was recovered from the artwork (every pixel is a blend of black and one blue ink,
so alpha = brightness, colour = ink) and the tile made landscape to match the
shield's ~1.5:1 aspect.

## Still out of scope

Genuine fuzzy (typo-tolerant) matching. Terms must each appear as a substring
somewhere in the name or key — "recipy" still finds nothing. Revisit if testers
hit it in practice.
