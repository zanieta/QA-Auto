// Footer: status sentence + the gated action buttons.
//
// Gate rules:
//   "View report"            — disabled during a run; enabled when done
//   "Log failures to Jira"   — disabled during a run AND when zero failures
//   "Push results to QMetry" — disabled during a run, until the run is done,
//     AND unless every case passed (zero failed, zero blocked) — QMetry
//     accepts an all-pass run only, so a run with any fail/blocked case keeps
//     the button disabled with a title naming why, never a soft warning.
//     ABSENT ENTIRELY (not present-but-disabled) when the run's plan is a
//     standalone "TC:<case key>" library plan — there is no execution to
//     write into, matching ManualView's `standalone` rule.
//     Clicking it asks the tester whether to write to the CURRENT execution or
//     CREATE a new one; the choice is sent to the backend. While pushing it
//     shows a spinner + "Pushing…"; the result shows inline, red on error —
//     "Pushed N cases · N steps · N errors", naming affected cases when
//     step_errors or errors is non-zero. A successful push also refetches the
//     rail's QMetry data (via `onPushed`) so the status dots/pills reflect
//     what QMetry now holds.
// The backend enforces the same gates; this UI gate is part of the design.

import { useState } from 'react'

export default function StageFoot({ state, activeCase, onReport, onLogBugs, onPushQmetry, onPushed }) {
  const status = state?.status ?? 'idle'
  const failed = state?.summary?.failed ?? 0
  const blocked = state?.summary?.blocked ?? 0
  const isRunning = status === 'running'
  const isDone = status === 'done'
  const isStandalonePlan = Boolean(state?.plan?.key?.startsWith('TC:'))
  const notAllPassed = failed > 0 || blocked > 0

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
      const { text, failed: hadErrors } = describePush(r)
      setPushMsg(text)
      setPushFailed(hadErrors)
      await onPushed?.() // refetch so the rail's QMetry dots/pills show the write
    } catch (e) {
      setPushFailed(true)
      setPushMsg(e.message)
    } finally {
      setPushing(false)
    }
  }

  const pushDisabled = isRunning || !isDone || notAllPassed
  const pushTitle = notAllPassed
    ? `${failed + blocked} case(s) did not pass — QMetry accepts an all-pass run only`
    : 'Write per-step Pass/Fail results to QMetry'

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

        {isStandalonePlan ? null : pushing ? (
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
            title={pushTitle}
          >
            Push results to QMetry
          </button>
        )}
      </div>
    </div>
  )
}

// Builds the push result line from the backend's response shape:
// { pushed, skipped, errors, details: [{case, exec_id, steps_written,
// step_errors: []}], steps_written, step_errors }. Names the affected cases
// when anything went wrong so a run where every step silently failed cannot
// read as clean.
function describePush(r) {
  const n = r.pushed.length
  const steps = r.steps_written ?? 0
  const stepErrCount = r.step_errors ?? 0
  const caseErrCount = r.errors?.length ?? 0
  const totalErrors = stepErrCount + caseErrCount
  const base = `Pushed ${n} case${n === 1 ? '' : 's'} · ${steps} step${steps === 1 ? '' : 's'} · ${totalErrors} error${totalErrors === 1 ? '' : 's'}`
  if (totalErrors === 0) return { text: base, failed: false }
  const affected = new Set()
  ;(r.errors ?? []).forEach((e) => affected.add(e.case))
  ;(r.details ?? []).forEach((d) => {
    if ((d.step_errors ?? []).length) affected.add(d.case)
  })
  const names = [...affected].join(', ')
  return { text: names ? `${base} — ${names}` : base, failed: true }
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
