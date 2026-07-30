// Total · Passed (green) · Failed (red) · Elapsed (mono).
// Elapsed live-increments on the client during a run so it ticks every frame
// rather than waiting on the poll.

import { useEffect, useState } from 'react'

export default function StatStrip({ state }) {
  const summary = state?.summary ?? { total: 0, passed: 0, failed: 0 }
  const baseElapsed = state?.elapsed_seconds ?? 0
  const live = useLiveElapsed(baseElapsed, state?.status === 'running')

  return (
    <div className="stat-strip" role="group" aria-label="Run statistics">
      <Stat label="Total" value={summary.total} />
      <Stat label="Passed" value={summary.passed} variant="pass" />
      <Stat label="Failed" value={summary.failed} variant="fail" />
      <Stat label="Elapsed" value={formatElapsed(live)} mono />
    </div>
  )
}

function Stat({ label, value, variant, mono }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value${variant ? ' ' + variant : ''}`}>{value}</span>
    </div>
  )
}

function formatElapsed(seconds) {
  if (!seconds || seconds < 0) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// Locally ticks elapsed once a second while the run is active so the UI
// doesn't look stuck between polls.
function useLiveElapsed(baseSeconds, running) {
  const [elapsed, setElapsed] = useState(baseSeconds)
  useEffect(() => {
    setElapsed(baseSeconds)
    if (!running) return
    const start = Date.now()
    const id = setInterval(() => {
      setElapsed(baseSeconds + (Date.now() - start) / 1000)
    }, 1000)
    return () => clearInterval(id)
  }, [baseSeconds, running])
  return elapsed
}
