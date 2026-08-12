// The signature element. Renders the steps of the active case and keeps the
// newest step in view as it streams in.

import { useEffect, useRef } from 'react'
import Step from './Step.jsx'

export default function ExecutionTape({ activeCase }) {
  const wrapRef = useRef(null)
  const steps = activeCase?.steps ?? []

  // Auto-scroll the tape to the bottom whenever a new step appears or resolves.
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [steps.length, steps.map((s) => s.status).join('|')])

  return (
    <div className="tape-wrap" ref={wrapRef}>
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
      <div className="tape-section-label">Execution tape</div>
      {steps.length === 0 ? (
        <div className="tape-empty">
          {activeCase
            ? 'No run yet. Press Run plan to start.'
            : 'Select a test case to view its tape.'}
        </div>
      ) : (
        steps.map((step, i) => <Step key={i} step={step} />)
      )}
    </div>
  )
}
