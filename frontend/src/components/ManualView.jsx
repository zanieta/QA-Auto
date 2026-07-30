// frontend/src/components/ManualView.jsx
// Manual-mode stage: stat strip + the selected case panel.
// Case list + selection live in the left rail (owned by App); this view receives
// the shared manual session state and the active case id as props.

import { useMemo, useState } from 'react'

import ManualCase from './ManualCase.jsx'
import { pushToQmetry } from '../hooks/useManualState.js'

export default function ManualView({ plan, planLabel, state, error, loading, refresh, activeId }) {
  const [pushing, setPushing] = useState(false)
  const [pushMsg, setPushMsg] = useState(null)
  const [pushFailed, setPushFailed] = useState(false)

  const activeCase = useMemo(
    () => state?.cases?.find((c) => c.id === activeId) ?? null,
    [state, activeId],
  )
  const summary = state?.summary ?? { total: 0, passed: 0, failed: 0, blocked: 0, unmarked: 0 }
  const anyMarked = summary.total - summary.unmarked > 0
  const agentRunning = state?.cases?.some((c) => c.manual.agent_status === 'running')
  const pushEnabled = state?.qmetry_configured && anyMarked && !agentRunning && !pushing
  const [choosing, setChoosing] = useState(false)

  async function handlePush(mode) {
    if (!pushEnabled) return
    const cycle = planLabel ?? plan ?? 'this cycle'
    const msg =
      mode === 'edit'
        ? `Write results to the EXISTING execution of ${cycle}? This replaces its current results.`
        : `Create a NEW execution in ${cycle} and write the results there?`
    if (!window.confirm(msg)) return // always confirm before writing to QMetry
    setChoosing(false)
    setPushing(true)
    setPushMsg(null)
    setPushFailed(false)
    try {
      const res = await pushToQmetry(plan, mode)
      setPushMsg(`Pushed ${res.pushed.length} · skipped ${res.skipped.length} · errors ${res.errors.length}`)
      await refresh?.()
    } catch (e) {
      setPushMsg(e.message)
      setPushFailed(true)
    } finally {
      setPushing(false)
    }
  }

  const pushTitle = !state?.qmetry_configured
    ? 'Connect QMetry to push results'
    : !anyMarked
      ? 'Mark at least one case first'
      : agentRunning
        ? 'Wait for the agent run to finish'
        : 'Push manual results to the QMetry cycle'

  return (
    <div className="manual">
      <div className="stat-strip">
        <Stat label="Total" value={summary.total} />
        <Stat label="Passed" value={summary.passed} cls="green" />
        <Stat label="Failed" value={summary.failed} cls="red" />
        <Stat label="Blocked" value={summary.blocked} cls="amber" />
        <Stat label="Remaining" value={summary.unmarked} />
      </div>

      {!state && loading ? (
        <p className="manual-empty">
          Loading cycle <span className="mono">{planLabel ?? plan}</span> — fetching test cases from QMetry…
        </p>
      ) : !state && error ? (
        <div role="alert" className="toast-error">
          Could not load cycle {planLabel ?? plan}: {error}
        </div>
      ) : activeCase ? (
        <ManualCase plan={plan} testCase={activeCase} onChanged={refresh} />
      ) : (
        <p className="manual-empty">No cases in this cycle yet.</p>
      )}

      <footer className="stage-foot">
        <span className={`status-line ${pushFailed ? 'error' : ''}`}>
          <span className={`status-dot ${pushFailed ? 'fail' : state?.qmetry_configured ? 'done' : 'idle'}`} />
          {pushMsg ?? (state?.qmetry_configured ? 'Marks save as you go.' : 'QMetry not connected — marks are local.')}
        </span>
        {pushing ? (
          <button type="button" className="btn btn-primary" disabled aria-busy="true">
            <span className="spinner" aria-hidden="true" />
            Pushing…
          </button>
        ) : choosing ? (
          <>
            <button type="button" className="btn btn-secondary" onClick={() => handlePush('edit')}>
              Current execution
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => handlePush('create')}>
              New execution
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setChoosing(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button
            type="button"
            className="btn btn-primary"
            disabled={!pushEnabled}
            title={pushTitle}
            onClick={() => setChoosing(true)}
          >
            Push results to QMetry
          </button>
        )}
      </footer>
    </div>
  )
}

function Stat({ label, value, cls }) {
  return (
    <div className="stat">
      <div className={`stat-num ${cls ?? ''}`}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}
