// frontend/src/components/StartPanel.jsx
// Clean-start stage: shown in place of the Manual/Live views on BOTH tabs
// until the tester picks a test run. Purely presentational — App owns the
// plan-selection state and passes onSelectCycle (== selectPlan), which rewrites
// the URL to ?cycle=<idOrKey> and loads it exactly as if the tester had
// bookmarked that link.

import { useState } from 'react'

export default function StartPanel({
  defaultCycle,
  defaultCycleKey,
  defaultCycleName,
  onSelectCycle,
}) {
  const [pasted, setPasted] = useState('')

  function handleOpen(e) {
    e.preventDefault()
    const value = pasted.trim()
    if (value) onSelectCycle?.(value)
  }

  return (
    <div className="start-panel-wrap">
      <div className="start-panel">
        <div className="start-panel-shield" aria-hidden="true">
          <img
            src="/duke-logo.png"
            alt=""
            onError={(e) => {
              e.currentTarget.style.display = 'none'
              e.currentTarget.parentElement.textContent = 'Duke'
            }}
          />
        </div>

        <h1 className="start-panel-title">QA Agent</h1>
        <p className="start-panel-lead">Choose a test run to begin</p>
        <p className="start-panel-hint">
          Use <strong>Browse</strong> in the left rail to search test runs or a single
          test case — by name or by key (<span className="mono">TR-434</span>,{' '}
          <span className="mono">TC-2075</span>) — or paste a test run key or id below.
        </p>

        <form className="start-panel-form" onSubmit={handleOpen}>
          <input
            type="text"
            className="start-panel-input mono"
            placeholder="Test run key or id, e.g. SOUSCLOUD-TR-482"
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            aria-label="Test run key or id"
          />
          <button type="submit" className="btn btn-secondary" disabled={!pasted.trim()}>
            Open
          </button>
        </form>

        {defaultCycle && (
          <button
            type="button"
            className="btn btn-primary start-panel-continue"
            onClick={() => onSelectCycle?.(defaultCycle)}
            title={defaultCycleName ? `${defaultCycleKey ?? defaultCycle} — ${defaultCycleName}` : undefined}
          >
            {/* Name the test run, never the internal QMetry cycle id.
                `defaultCycle` is an id like "1ZwYH2ObF7AGZa", which means
                nothing to a tester and is not what QMetry's own UI shows.
                The id remains the value we open with; it just isn't the
                label. Falls back to the id when the key could not be
                resolved, so the button is never blank. */}
            Continue with{' '}
            <span className="mono">{defaultCycleKey ?? defaultCycle}</span>
            {defaultCycleName && (
              <span className="start-panel-continue-name">{defaultCycleName}</span>
            )}
          </button>
        )}
      </div>
    </div>
  )
}
