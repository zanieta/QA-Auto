import { useEffect, useRef, useState } from 'react'

// Mode A polling. When runId is null we poll the fixture so the UI works
// before the backend exists. When runId is set we poll the real endpoint
// (proxied to server.py on :8000 via vite.config.js).
//
// Replace with EventSource subscription once Mode B (SSE) is wired.

const FIXTURE_URL = '/fixtures/sample_run_state.json'
const POLL_MS = 500

export function useRunState(runId) {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)
  const timer = useRef(null)

  useEffect(() => {
    const url = runId ? `/runs/${runId}` : FIXTURE_URL
    let cancelled = false

    async function tick() {
      try {
        const res = await fetch(url, { cache: 'no-store' })
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const json = await res.json()
        if (!cancelled) {
          setState(json)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }

    tick()
    timer.current = setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer.current)
    }
  }, [runId])

  return { state, error }
}

// Convenience: kicks off a real run. Returns { run_id }.
// `credentials` is an optional { username, password }; both must be non-empty
// to be sent at all, otherwise the backend uses the .env admin account.
export async function startRun(planKey, credentials) {
  const body = { plan: planKey }
  if (credentials?.username && credentials?.password) {
    body.username = credentials.username
    body.password = credentials.password
  }
  const res = await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Failed to start run: ${res.status}`)
  return res.json()
}

export async function requestReport(runId) {
  const res = await fetch(`/runs/${runId}/report`, { method: 'POST' })
  if (!res.ok) throw new Error(`Report failed: ${res.status}`)
  return res.json()
}

export async function logFailuresToJira(runId) {
  const res = await fetch(`/runs/${runId}/log-bugs`, { method: 'POST' })
  if (!res.ok) throw new Error(`Log bugs failed: ${res.status}`)
  return res.json()
}

export async function pushRunToQmetry(runId, mode) {
  const opts = { method: 'POST' }
  if (mode) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify({ mode })
  }
  const res = await fetch(`/runs/${runId}/push-qmetry`, opts)
  if (!res.ok) {
    let detail = `Push failed: ${res.status}`
    try {
      detail = (await res.json()).detail || detail
    } catch {
      /* non-JSON error body — keep the status message */
    }
    throw new Error(detail)
  }
  return res.json()
}
