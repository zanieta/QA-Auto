// One step card: marker + body + timing + optional screenshot thumbnail.
// Markers: running (spinner) / pass (✓) / fail (✕) / blocked (!).

import { useState } from 'react'

export default function Step({ step }) {
  const { action, detail, status, evaluation, duration_seconds, screenshot_b64, test_data } = step
  const [imgOpen, setImgOpen] = useState(false)

  return (
    <article className="step" aria-live="polite">
      <Marker status={status} />
      <div className="step-body">
        <div className="step-action">{action}</div>
        {detail && <div className="step-detail">{detail}</div>}
        <div className="step-test-data">
          <span className="step-test-data-label">Test data</span>{' '}
          {test_data ? (
            <span className="mono">{test_data}</span>
          ) : (
            <em>none</em>
          )}
        </div>
        {evaluation && (
          <div className={`step-eval ${status}`}>{evaluation}</div>
        )}
        {screenshot_b64 && status !== 'running' && (
          <div className="step-screenshot">
            <button
              type="button"
              className="step-screenshot-toggle"
              onClick={() => setImgOpen((o) => !o)}
              aria-expanded={imgOpen}
            >
              {imgOpen ? '▲ Hide screenshot' : '▼ Show screenshot'}
            </button>
            {imgOpen && (
              <img
                className="step-screenshot-img"
                src={`data:image/png;base64,${screenshot_b64}`}
                alt={`Screenshot after: ${action}`}
              />
            )}
          </div>
        )}
      </div>
      <div className="step-time">{formatTime(duration_seconds, status)}</div>
    </article>
  )
}

function Marker({ status }) {
  if (status === 'running') {
    return (
      <div className="step-marker running" aria-label="Running">
        <div className="spinner" />
      </div>
    )
  }
  const symbol = status === 'pass' ? '✓' : status === 'fail' ? '✕' : '!'
  return (
    <div className={`step-marker ${status}`} aria-label={status}>
      {symbol}
    </div>
  )
}

function formatTime(seconds, status) {
  if (status === 'running' || seconds == null) return '···'
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`
  return `${seconds.toFixed(1)}s`
}
