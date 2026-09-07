// frontend/src/components/ManualView.jsx
// Manual-mode stage: stat strip + the selected case panel.
// Case list + selection live in the left rail (owned by App); this view receives
// the shared manual session state and the active case id as props.

import { useEffect, useMemo, useRef, useState } from 'react'

import ManualCase from './ManualCase.jsx'
import { fetchCaseSteps, pushToQmetry } from '../hooks/useManualState.js'

export default function ManualView({
  plan,
  planLabel,
  state,
  error,
  loading,
  refresh,
  activeId,
}) {
  const [pushing, setPushing] = useState(false)
  const [pushMsg, setPushMsg] = useState(null)
  const [pushFailed, setPushFailed] = useState(false)
  const [stepsError, setStepsError] = useState(null)

  const activeCase = useMemo(
    () => state?.cases?.find((c) => c.id === activeId) ?? null,
    [state, activeId],
  )

  // Steps load per case, on open. The in-flight set keeps a refresh (each mark
  // triggers one) from firing a second fetch for the same case.
  const hydrating = useRef(new Set())
  useEffect(() => {
    if (!plan || !activeCase || activeCase.steps_loaded) return
    const key = `${plan}::${activeCase.id}`
    if (hydrating.current.has(key)) return
    hydrating.current.add(key)
    setStepsError(null)
    fetchCaseSteps(plan, activeCase.id)
      .then(() => refresh?.())
      .catch((e) => setStepsError(e.message))
      .finally(() => hydrating.current.delete(key))
  }, [plan, activeCase, refresh])
  const summary = state?.summary ?? { total: 0, passed: 0, failed: 0, blocked: 0, unmarked: 0 }
  const anyMarked = summary.total - summary.unmarked > 0
  // QMetry accepts an all-pass run only: any MARKED case that is fail or
  // blocked blocks the push, same rule as the Live tab's StageFoot.
  const anyMarkedNotPassed = Boolean(
    state?.cases?.some((c) => c.manual.status === 'fail' || c.manual.status === 'blocked'),
  )
  const agentRunning = state?.cases?.some((c) => c.manual.agent_status === 'running')
  // A library test case has no execution to write into, so there is nothing to
  // push — the control is absent rather than present-but-disabled.
  const standalone = Boolean(state?.standalone)
  const pushEnabled =
    state?.qmetry_configured &&
    !standalone &&
    anyMarked &&
    !anyMarkedNotPassed &&
    !agentRunning &&
    !pushing
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
      const { text, failed: hadErrors } = describePush(res)
      setPushMsg(text)
      setPushFailed(hadErrors)
      await refresh?.() // refetch so the rail's QMetry dots/pills show the write
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
      : anyMarkedNotPassed
        ? 'A marked case is fail or blocked — QMetry accepts an all-pass run only'
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
      ) : stepsError ? (
        <div role="alert" className="toast-error">
          Could not load the steps for {activeId}: {stepsError}
        </div>
      ) : activeCase && !activeCase.steps_loaded ? (
        <p className="manual-empty">
          Loading steps for <span className="mono">{activeCase.id}</span>…
        </p>
      ) : activeCase ? (
        <ManualCase
          plan={plan}
          testCase={activeCase}
          onChanged={refresh}
        />
      ) : (
        <p className="manual-empty">No cases in this cycle yet.</p>
      )}

      <footer className="stage-foot">
        <span className={`status-line ${pushFailed ? 'error' : ''}`}>
          <span className={`status-dot ${pushFailed ? 'fail' : state?.qmetry_configured ? 'done' : 'idle'}`} />
          {pushMsg ??
            (standalone
              ? 'Library test case — marks and agent runs stay local, nothing is written to QMetry.'
              : state?.qmetry_configured
                ? 'Marks save as you go.'
                : 'QMetry not connected — marks are local.')}
        </span>
        {standalone ? null : pushing ? (
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

// Builds the push result line from the backend's response shape:
// { pushed, skipped, errors, details: [{case, exec_id, steps_written,
// step_errors: []}], steps_written, step_errors }. Names the affected cases
// when anything went wrong so a run where every step silently failed cannot
// read as clean. Mirrors StageFoot.jsx's describePush (Live tab).
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

function Stat({ label, value, cls }) {
  return (
    <div className="stat">
      <div className={`stat-num ${cls ?? ''}`}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}
