// frontend/src/hooks/useManualState.js
import { useCallback, useEffect, useState } from 'react'

// GET /manual/{plan}. No plan key means no test run has been chosen yet — the
// clean-start StartPanel owns the screen in that state, so this hook must not
// fetch anything (not even the demo fixture) until a real plan key exists. A
// real cycle that fails to load shows its error; it must never silently
// impersonate data.

export function useManualState(planKey) {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!planKey) {
      setState(null)
      setError(null)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const res = await fetch(`/manual/${encodeURIComponent(planKey)}`, { cache: 'no-store' })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `${res.status} ${res.statusText}`)
      }
      setState(await res.json())
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [planKey])

  useEffect(() => {
    // Switching cycles: drop the old cycle's data immediately so it can't be
    // mistaken for the new one while it loads.
    setState(null)
    setError(null)
    refresh()
  }, [refresh])

  return { state, error, loading, refresh }
}

export async function markCase(planKey, caseId, body) {
  const res = await fetch(
    `/manual/${encodeURIComponent(planKey)}/cases/${encodeURIComponent(caseId)}/mark`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
  if (!res.ok) throw new Error(`Mark failed: ${res.status}`)
  return res.json()
}

export async function markStep(planKey, caseId, stepIndex, body) {
  const res = await fetch(
    `/manual/${encodeURIComponent(planKey)}/cases/${encodeURIComponent(caseId)}/steps/${stepIndex}/mark`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `Step mark failed: ${res.status}`)
  }
  return res.json()
}

export async function runAgentCase(planKey, caseId, steps = null) {
  const opts = { method: 'POST' }
  if (Array.isArray(steps)) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify({ steps })
  }
  const res = await fetch(
    `/manual/${encodeURIComponent(planKey)}/cases/${encodeURIComponent(caseId)}/run-agent`,
    opts,
  )
  if (!res.ok) throw new Error(`Run agent failed: ${res.status}`)
  return res.json()
}

export async function saveCaseCredentials(planKey, caseId, username, password) {
  const res = await fetch(
    `/manual/${encodeURIComponent(planKey)}/cases/${encodeURIComponent(caseId)}/credentials`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    },
  )
  if (!res.ok) throw new Error(`Credentials save failed: ${res.status}`)
  return res.json()
}

export async function cancelRun(runId) {
  const res = await fetch(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `Cancel failed: ${res.status}`)
  }
  return res.json()
}

export async function pushToQmetry(planKey, mode) {
  const opts = { method: 'POST' }
  if (mode) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify({ mode })
  }
  const res = await fetch(`/manual/${encodeURIComponent(planKey)}/push-qmetry`, opts)
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `Push failed: ${res.status}`)
  }
  return res.json()
}
