// Footer: status sentence + the gated action buttons.
//
// Gate rules:
//   "View report"            — disabled during a run; enabled when done
//   "Log failures to Jira"   — disabled during a run AND when zero failures
//   "Push results to QMetry" — disabled during a run and until the run is done.
//     Clicking it asks the tester whether to write to the CURRENT execution or
//     CREATE a new one; the choice is sent to the backend. While pushing it
//     shows a spinner + "Pushing…"; the result shows inline, red on error.
// The backend enforces the same gates; this UI gate is part of the design.

import { useState } from 'react'

export default function StageFoot({ state, activeCase, onReport, onLogBugs, onPushQmetry }) {
  const status = state?.status ?? 'idle'
  const failed = state?.summary?.failed ?? 0
  const isRunning = status === 'running'
  const isDone = status === 'done'

  const [choosing, setChoosing] = useState(false)
  const [pushing, setPushing] = useState(false)
  const [pushMsg, setPushMsg] = useState(null)
  const [pushFailed, setPushFailed] = useState(false)

  async function doPush(mode) {
    if (!onPushQmetry) return
    const cycle = state?.plan?.key ?? 'this cycle'
    const msg =
      mode === 'edit'
        ? `Write results to the EXISTING execution of ${cycle}? This replaces its current results.`
        : `Create a NEW execution in ${cycle} and write the results there?`
    if (!window.confirm(msg)) return // always confirm before writing to QMetry
    setChoosing(false)
    setPushing(true)
    setPushFailed(false)
    setPushMsg(null)
    try {
      const r = await onPushQmetry(mode)
      const n = r.errors.length
      setPushMsg(
        `Pushed ${r.pushed.length}, skipped ${r.skipped.length}, ${n} error${n === 1 ? '' : 's'}`,
      )
    } catch (e) {
      setPushFailed(true)
      setPushMsg(e.message)
    } finally {
      setPushing(false)
    }
  }

  const pushDisabled = isRunning || !isDone

  return (
    <div className="stage-foot">
      <div className="foot-status">
        <span className={`foot-status-dot ${status}`} />
        <span>{statusSentence(state, activeCase)}</span>
      </div>
      <div className="foot-actions">
        {pushMsg && (
          <span
            className={`status-line ${pushFailed ? 'error' : ''}`}
            role={pushFailed ? 'alert' : 'status'}
          >
            {pushMsg}
          </span>
        )}
        <button
          type="button"
          className="btn btn-secondary"
          disabled={isRunning || !isDone}
          onClick={onReport}
        >
          View report
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={isRunning || !isDone || failed === 0}
          onClick={onLogBugs}
          title={
            failed === 0
              ? 'No failures to log'
              : 'Create Jira bugs for failed test cases'
          }
        >
          Log failures to Jira
        </button>

        {pushing ? (
          <button type="button" className="btn btn-secondary" disabled aria-busy="true">
            <span className="spinner" aria-hidden="true" />
            Pushing…
          </button>
        ) : choosing ? (
          <>
            <span className="status-line">Write results to:</span>
            <button type="button" className="btn btn-secondary" onClick={() => doPush('edit')}>
              Current execution
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => doPush('create')}>
              New execution
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setChoosing(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button
            type="button"
            className="btn btn-secondary"
            disabled={pushDisabled}
            onClick={() => setChoosing(true)}
            title="Write per-step Pass/Fail results to QMetry"
          >
            Push results to QMetry
          </button>
        )}
      </div>
    </div>
  )
}

function statusSentence(state, activeCase) {
  const status = state?.status ?? 'idle'
  const failed = state?.summary?.failed ?? 0

  if (status === 'idle') {
    return 'Ready to run. Press Run plan to start.'
  }
  if (status === 'running') {
    if (activeCase) return `Running ${activeCase.id} — ${activeCase.name}`
    return 'Running…'
  }
  if (failed === 0) return 'Run complete — all cases passed.'
  if (failed === 1) return 'Run complete — 1 failure needs attention.'
  return `Run complete — ${failed} failures need attention.`
}
