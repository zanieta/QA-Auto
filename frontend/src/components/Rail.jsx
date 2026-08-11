// Left rail. Two states:
//
//   browse     — the TR|TC picker (CaseBrowser). Nothing is open yet, or a
//                library test case is open (TC mode keeps the list in view so
//                the tester can walk down it).
//   drilled in — a test run is open: back link, run meta with progress, and
//                that run's cases. The active case row gets a translucent
//                white background.

import CaseBrowser from './CaseBrowser.jsx'

export default function Rail({
  state,
  activeId,
  onSelectCase,
  mode,
  onModeChange,
  onPick,
  drilledIn,
  onBack,
  browseActiveKey,
}) {
  const summary = state?.summary ?? { total: 0, passed: 0, failed: 0, blocked: 0 }
  const cases = state?.test_cases ?? []
  const done = summary.passed + summary.failed + (summary.blocked ?? 0)
  const pct = summary.total ? Math.round((done / summary.total) * 100) : 0

  return (
    <aside className="rail" aria-label="Plan navigation">
      <div className="rail-brand">
        <div className="rail-shield" aria-hidden="true">
          {/* Drop frontend/public/duke-logo.png to swap the placeholder. */}
          <img
            src="/duke-logo.png"
            alt=""
            onError={(e) => {
              e.currentTarget.style.display = 'none'
              e.currentTarget.parentElement.textContent = 'Duke'
            }}
          />
        </div>
        <div>
          <div className="rail-title">QA Agent</div>
          <div className="rail-subtitle">Sous Chef Cloud</div>
        </div>
      </div>

      {!drilledIn ? (
        <CaseBrowser
          mode={mode}
          onModeChange={onModeChange}
          activeKey={browseActiveKey}
          onPick={onPick}
        />
      ) : (
        <>
          <button type="button" className="rail-back" onClick={onBack}>
            ← All test runs
          </button>

          <div className="rail-plan">
            <div className="rail-plan-key mono">{state?.plan?.key ?? '—'}</div>
            <div className="rail-plan-name">{state?.plan?.name ?? 'No plan selected'}</div>
            <div className="rail-progress">
              <div className="rail-progress-bar">
                <div className="rail-progress-fill" style={{ width: `${pct}%` }} />
              </div>
              <div className="rail-progress-text">
                <span>
                  {done} / {summary.total}
                </span>
                <span>{pct}%</span>
              </div>
            </div>
          </div>

          <div className="rail-section-label">Test cases</div>
          <div className="rail-cases" role="list">
            {cases.length === 0 && (
              <div className="browser-msg">Loading test cases…</div>
            )}
            {cases.map((c) => (
              <button
                key={c.id}
                type="button"
                role="listitem"
                className={`case-row ${activeId === c.id ? 'active' : ''}`}
                onClick={() => onSelectCase?.(c.id)}
              >
                <CaseDot status={c.status} />
                <span className="case-row-id">{c.id}</span>
                <span className="case-row-name">{c.name}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </aside>
  )
}

function CaseDot({ status }) {
  const symbol =
    status === 'pass' ? '✓' : status === 'fail' ? '✕' : status === 'blocked' ? '!' : ''
  return (
    <span className={`case-dot ${status}`} aria-label={status}>
      {symbol}
    </span>
  )
}
