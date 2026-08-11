// frontend/src/components/CaseBrowser.jsx
// The rail's browse state: a "browse what" picker, a search box, and one page of
// results with a Load more button.
//
// Test runs and test cases are alternatives, not two things to compare, so this
// is a single select rather than side-by-side tabs — one control, one answer.
//
// Search notes (they drive the copy here): QMetry's search is a single
// substring, so the backend expands key-shaped queries into an exact key lookup
// and ANDs multi-word queries itself. `truncated` means it stopped scanning at
// its cap, so the count is a floor.

import { useCatalog } from '../hooks/useCatalog.js'

const MODES = [
  { key: 'tr', label: 'Test runs', noun: 'test runs', example: 'e.g. regression, or TR-434' },
  { key: 'tc', label: 'Test cases', noun: 'test cases', example: 'e.g. delete recipe, or TC-2075' },
]

export default function CaseBrowser({ mode, onModeChange, activeKey, onPick }) {
  const { query, setQuery, items, total, loading, error, hasMore, truncated, loadMore } =
    useCatalog(mode)

  const current = MODES.find((m) => m.key === mode) ?? MODES[0]
  const searching = query.trim().length > 0
  // Only claim "no matches" once a request has actually settled — saying it
  // mid-flight reads as a failed search.
  const empty = !loading && !error && items.length === 0

  return (
    <div className="browser">
      <div className="browser-pick">
        <label className="browser-pick-label" htmlFor="browse-mode">
          Browse
        </label>
        <select
          id="browse-mode"
          className="browser-pick-select"
          value={mode}
          onChange={(e) => onModeChange(e.target.value)}
        >
          {MODES.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </select>
      </div>

      <div className="browser-search">
        <span className="browser-search-icon" aria-hidden="true">
          ⌕
        </span>
        {/* type="text", not "search": Chrome's native search decoration renders
            an off-palette clear glyph that reads as a broken dropdown arrow. */}
        <input
          id="browse-search"
          type="text"
          className="browser-search-input"
          placeholder={`Search ${current.noun}…`}
          aria-label={`Search ${current.noun}`}
          autoComplete="off"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {searching && (
          <button
            type="button"
            className="browser-search-clear"
            aria-label="Clear search"
            onClick={() => setQuery('')}
          >
            ✕
          </button>
        )}
      </div>
      {!searching && <p className="browser-hint">{current.example}</p>}

      <div className="browser-results" role="list">
        {error && (
          <div role="alert" className="browser-msg error">
            {error}
          </div>
        )}

        {empty && (
          <div className="browser-msg">
            {searching ? `No ${current.noun} match “${query.trim()}”.` : `No ${current.noun} found.`}
          </div>
        )}

        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            role="listitem"
            className={`browser-row ${activeKey === item.key ? 'active' : ''}`}
            onClick={() => onPick(item)}
            title={`${item.key} — ${item.name}`}
          >
            <span className="browser-row-key mono">{item.key}</span>
            {/* Two lines, not one: run names share long prefixes ("Sous Chef
                Cloud vX.X.X — …"), so a single truncated line makes different
                runs read identically. */}
            <span className="browser-row-name">{item.name}</span>
          </button>
        ))}

        {loading && (
          <div className="browser-msg">
            <span className="spinner" aria-hidden="true" /> Searching…
          </div>
        )}
      </div>

      <div className="browser-foot">
        <span className="browser-count mono">
          {items.length} of {total}
          {truncated ? '+' : ''}
        </span>
        {hasMore && (
          <button
            type="button"
            className="browser-more"
            disabled={loading}
            onClick={loadMore}
          >
            Load more
          </button>
        )}
      </div>
    </div>
  )
}

