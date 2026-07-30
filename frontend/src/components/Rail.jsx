// Left rail: brand + plan meta (with cycle picker) + test case list.
// The active case row gets a translucent white background.

export default function Rail({ state, activeId, onSelectCase, cycles = [], currentCycle, onSelectCycle }) {
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

      <div className="rail-section-label">Plan</div>
      <div className="rail-plan">
        {cycles.length > 0 ? (
          <select
            className="rail-cycle-select"
            aria-label="Test cycle"
            value={currentCycle ?? ''}
            onChange={(e) => onSelectCycle?.(e.target.value)}
          >
            {/* Clean-start state: nothing chosen yet — show a disabled
                placeholder instead of pre-selecting a real cycle. */}
            {!currentCycle && (
              <option value="" disabled>
                — choose test run —
              </option>
            )}
            {/* Keep the current selection listed even when it's not in the
                newest page of cycles. QMetry exposes keys only — no names. */}
            {currentCycle && !cycles.some((c) => c.id === currentCycle || c.key === currentCycle) && (
              <option value={currentCycle}>{currentCycle}</option>
            )}
            {cycles.map((c) => (
              <option key={c.id} value={c.id}>
                {c.key}
              </option>
            ))}
          </select>
        ) : (
          <div className="rail-plan-key">{state?.plan?.key ?? '—'}</div>
        )}
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
        {/* Clean-start state: no TR chosen — leave the list area empty
            instead of showing "No cases loaded." noise. */}
        {cases.length === 0 && currentCycle && (
          <div style={{ padding: '12px 16px', color: 'rgba(255,255,255,0.5)', fontSize: 12 }}>
            No cases loaded.
          </div>
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
