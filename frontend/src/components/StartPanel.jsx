// frontend/src/components/StartPanel.jsx
// Clean-start stage: shown in place of the Manual/Live views on BOTH tabs
// until the tester picks a test run. Purely presentational — App owns the
// cycle-selection state and passes onSelectCycle (== handleSelectCycle),
// which rewrites the URL to ?cycle=<idOrKey> and loads it exactly as if the
// tester had bookmarked that link.

import { useState } from 'react'

export default function StartPanel({ defaultCycle, defaultCycleLabel, onSelectCycle }) {
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
          Pick a cycle from the <strong>Plan</strong> dropdown in the left rail, or
          paste a cycle id or key below.
        </p>

        <form className="start-panel-form" onSubmit={handleOpen}>
          <input
            type="text"
            className="start-panel-input mono"
            placeholder="Cycle id or key, e.g. jZYJHjkvCabDMD"
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            aria-label="Cycle id or key"
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
          >
            Continue with <span className="mono">{defaultCycleLabel || defaultCycle}</span>
          </button>
        )}
      </div>
    </div>
  )
}
