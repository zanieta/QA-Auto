// frontend/src/components/ManualCase.jsx
// One test case: read-only steps, per-step marking (Pass/Fail/Blocked/Skip)
// beside the agent chip, an overall notes field, and an optional inline
// agent run. The case's status is derived server-side from the step marks
// and shown as a read-only pill in the header.

import { useEffect, useRef, useState } from 'react'

import Step from './Step.jsx'
import { cancelRun, markCase, markStep, runAgentCase, saveCaseCredentials } from '../hooks/useManualState.js'
import { useRunState } from '../hooks/useRunState.js'

const STEP_MARKS = [
  { key: 'pass', label: 'Pass' },
  { key: 'fail', label: 'Fail' },
  { key: 'blocked', label: 'Blocked' },
  { key: 'skip', label: 'Skip' },
]

// QMetry/Jira steps come back as wiki markup. Strip the noisiest tokens so the
// text is readable; rendered with white-space: pre-wrap so line breaks survive.
function cleanMarkup(text) {
  if (!text) return ''
  return String(text)
    .replace(/\{panel[^}]*\}/gi, '') // {panel:bgColor=…} … {panel}
    .replace(/\{color[^}]*\}/gi, '')
    .replace(/!https?:\/\/[^!]*!/g, '[image]') // !image-url|width=…!
    .replace(/\{\{([^}]*)\}\}/g, '$1') // {{monospace}} → monospace
    .replace(/\[~[^\]]+\]/g, 'the user') // [~accountid] mention
    .replace(/^h[1-6]\.\s*/gim, '') // h4. heading prefix
    .replace(/^#\*+\s*/gim, '   • ') // nested list item
    .replace(/^#\s*/gim, '• ') // list item
    .replace(/^\*+\s+/gim, '• ') // * bullet at line start
    .replace(/\*([^*\n]+)\*/g, '$1') // *bold* → bold
    .replace(/\n{3,}/g, '\n\n') // collapse blank runs
    .trim()
}

export default function ManualCase({ plan, testCase, onChanged }) {
  const m = testCase.manual
  const allIndices = testCase.steps.map((_, i) => i)
  const [comment, setComment] = useState(m.comment || '')
  const [savingComment, setSavingComment] = useState(false)
  const [agentRunId, setAgentRunId] = useState(m.agent_run_id || null)
  const [runErr, setRunErr] = useState(null)
  const [cancelling, setCancelling] = useState(false)
  // which steps the agent SHOULD run (checkboxes) — all checked by default
  const [agentSel, setAgentSel] = useState(allIndices)
  // which original indices the last-started run covers (for chip mapping)
  const [lastRunSteps, setLastRunSteps] = useState(m.agent_steps ?? null)
  const [loginUser, setLoginUser] = useState(m.login_username || '')
  const [loginPw, setLoginPw] = useState('')
  const [credsMsg, setCredsMsg] = useState(null)

  // GUARD: useRunState(null) polls the demo fixture — never let fixture data
  // masquerade as a real agent run.
  const { state: rawAgentState } = useRunState(agentRunId)
  const agentState = agentRunId ? rawAgentState : null

  // Reset local form when switching cases.
  useEffect(() => {
    setComment(m.comment || '')
    setAgentRunId(m.agent_run_id || null)
    setAgentSel(testCase.steps.map((_, i) => i))
    setLastRunSteps(m.agent_steps ?? null)
    setLoginUser(m.login_username || '')
    setLoginPw('')
    setCredsMsg(null)
  }, [testCase.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const agentRunning =
    agentState?.status === 'running' ||
    (m.agent_status === 'running' && agentState == null)

  // map original step index -> resolved tape step (for the agent chip)
  const executed = lastRunSteps ?? allIndices
  const agentCase =
    agentState?.test_cases?.find((c) => c.id === testCase.id) ?? agentState?.test_cases?.[0]
  const chipByStep = {}
  agentCase?.steps?.forEach((s, i) => {
    const orig = executed[i]
    if (orig != null) chipByStep[orig] = s
  })

  function toggleAgentStep(i) {
    setAgentSel((prev) => (prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i].sort((a, b) => a - b)))
  }

  // The comment is the only thing this endpoint still sets from the UI — it
  // sends the server-derived status straight back so it never overwrites it.
  async function saveComment() {
    setSavingComment(true)
    try {
      await markCase(plan, testCase.id, {
        status: m.status,
        comment,
        failed_steps: m.failed_steps || [],
      })
      await onChanged?.()
    } finally {
      setSavingComment(false)
    }
  }

  async function handleStepMark(index, status, note, agentStatus) {
    await markStep(plan, testCase.id, index, { status, note, agent_status: agentStatus ?? null })
    await onChanged?.()
  }

  async function handleRunAgent() {
    setRunErr(null)
    const subset = agentSel.length < testCase.steps.length ? agentSel : null
    try {
      const { run_id } = await runAgentCase(plan, testCase.id, subset)
      setLastRunSteps(subset)
      setAgentRunId(run_id)
      await onChanged?.()
    } catch (e) {
      setRunErr(e.message)
    }
  }

  async function handleCancelAgent() {
    // Prefer the freshly-started run's id; fall back to the mark's persisted
    // id (e.g. after a refresh mid-run).
    const runId = agentRunId || m.agent_run_id
    if (!runId) return
    setCancelling(true)
    setRunErr(null)
    try {
      await cancelRun(runId)
      await onChanged?.()
    } catch (e) {
      setRunErr(e.message)
    } finally {
      setCancelling(false)
    }
  }

  async function handleSaveCredentials() {
    setCredsMsg(null)
    try {
      await saveCaseCredentials(plan, testCase.id, loginUser, loginPw)
      setLoginPw('')
      setCredsMsg(loginUser || loginPw ? 'saved' : 'cleared — using default admin')
      await onChanged?.()
    } catch (e) {
      setCredsMsg(e.message)
    }
  }

  return (
    <section className="manual-case">
      <header className="manual-case-head">
        <span className="stage-head-id">{testCase.id}</span>
        <h1 className="stage-head-title">{testCase.name}</h1>
        <span className={`case-status-pill ${m.status}`}>{m.status}</span>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={agentRunning || agentSel.length === 0}
          aria-busy={agentRunning ? 'true' : undefined}
          onClick={handleRunAgent}
        >
          {agentRunning ? (
            <>
              <span className="spinner" aria-hidden="true" />
              Agent running…
            </>
          ) : (
            '▶ Run selected steps with agent'
          )}
        </button>
        {agentRunning && (
          <button
            type="button"
            className="btn btn-ghost"
            disabled={cancelling}
            onClick={handleCancelAgent}
          >
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        )}
        {runErr && <span className="toast-error" role="alert">{runErr}</span>}
      </header>

      {testCase.precondition && (
        <div className="manual-precondition">
          <div className="manual-precondition-label">Precondition</div>
          <div className="manual-precondition-text">{testCase.precondition}</div>
        </div>
      )}

      <div className="manual-credentials">
        <span className="manual-credentials-label">Login as</span>
        <input
          type="text"
          placeholder="username (default admin)"
          value={loginUser}
          disabled={agentRunning}
          onChange={(e) => setLoginUser(e.target.value)}
        />
        <input
          type="password"
          placeholder={m.has_password ? '••• saved' : 'password'}
          value={loginPw}
          disabled={agentRunning}
          onChange={(e) => setLoginPw(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-ghost"
          disabled={agentRunning}
          onClick={handleSaveCredentials}
        >
          Save
        </button>
        {credsMsg && <span className="manual-credentials-msg">{credsMsg}</span>}
      </div>
      <p className="manual-credentials-help">Leave blank to use the system admin account.</p>

      <p className="manual-agent-hint">
        The agent starts from the dashboard after login — do unchecked earlier steps by hand first.
      </p>

      <ol className="manual-steps">
        {testCase.steps.map((s, i) => {
          const stepMark = m.step_marks?.[String(i)]
          const flagCls =
            stepMark?.status === 'fail' || stepMark?.status === 'blocked'
              ? `flagged ${stepMark.status}`
              : ''
          return (
            <li key={i} className={`manual-step ${flagCls}`}>
              <label className="manual-step-agent">
                <input
                  type="checkbox"
                  checked={agentSel.includes(i)}
                  disabled={agentRunning}
                  onChange={() => toggleAgentStep(i)}
                />
                <span>agent</span>
              </label>
              <span className="manual-step-no">{i + 1}</span>
              <div className="manual-step-body">
                <div className="manual-step-action">{cleanMarkup(s.action)}</div>
                {s.expected && (
                  <div className="manual-step-expected">{cleanMarkup(s.expected)}</div>
                )}
                <StepMarkRow
                  caseId={testCase.id}
                  index={i}
                  chip={chipByStep[i]}
                  mark={stepMark}
                  onMark={handleStepMark}
                />
              </div>
            </li>
          )
        })}
      </ol>

      <div className="manual-notes">
        <label htmlFor={`note-${testCase.id}`}>Notes</label>
        <textarea
          id={`note-${testCase.id}`}
          value={comment}
          placeholder="Overall notes for this case"
          disabled={savingComment}
          onChange={(e) => setComment(e.target.value)}
          onBlur={saveComment}
        />
      </div>

      {m.agent_note ? (
        <div className="agent-note" aria-label="Agent run notes">
          <div className="agent-note-label">Agent notes</div>
          <pre>{m.agent_note}</pre>
        </div>
      ) : null}


      {agentRunId && (
        <AgentTape
          key={agentRunId}
          state={agentState}
          caseId={testCase.id}
          onDone={onChanged}
          executed={executed}
          stepMarks={m.step_marks}
          onMark={handleStepMark}
        />
      )}
    </section>
  )
}

function AgentTape({ state, caseId, onDone, executed, stepMarks, onMark }) {
  const firedRef = useRef(false)

  useEffect(() => {
    if (state?.status === 'done' && !firedRef.current) {
      firedRef.current = true
      onDone?.()
    }
  }, [state?.status, onDone])

  const agentCase = state?.test_cases?.find((c) => c.id === caseId) ?? state?.test_cases?.[0]
  const steps = agentCase?.steps ?? []
  return (
    <div className="manual-agent-tape">
      <div className="section-label">Agent run · {agentCase?.status ?? 'running'}</div>
      {steps.map((s, i) => {
        // Tape position i maps back to the ORIGINAL step index so a verdict
        // given here lands on the same mark as the one in the steps list.
        const orig = executed?.[i]
        return (
          <div key={i} className="tape-entry">
            <Step step={s} />
            {orig != null && s.status !== 'running' && (
              <StepMarkRow
                caseId={caseId}
                index={orig}
                chip={s}
                mark={stepMarks?.[String(orig)]}
                onMark={onMark}
              />
            )}
          </div>
        )
      })}
      {steps.length === 0 && <div className="manual-step-expected">Agent is starting…</div>}
    </div>
  )
}

// Per-step mark: the agent chip plus four small Pass/Fail/Blocked/Skip
// buttons. A click matching the chip's verdict (or no chip at all) saves
// immediately; a click that contradicts it opens an inline note field —
// the override cannot be saved without a note.
function StepMarkRow({ caseId, index, chip, mark, onMark }) {
  const agentStatus = chip?.status ?? (mark?.agent_status ?? null)
  const [draft, setDraft] = useState(null) // { status, note } while overriding
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState(null)

  async function commit(status, note) {
    setErr(null)
    setSaving(true)
    try {
      await onMark(index, status, note, agentStatus)
      setDraft(null)
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  function handleClick(clickStatus) {
    const contradicts = agentStatus && clickStatus !== agentStatus
    if (contradicts) {
      setErr(null)
      setDraft({ status: clickStatus, note: '' })
      return
    }
    commit(clickStatus, '')
  }

  function handleSaveOverride() {
    if (!draft || !draft.note.trim()) return
    commit(draft.status, draft.note)
  }

  return (
    <div className="step-mark-block">
      <div className="step-mark-line">
        {chip && chip.status !== 'running' && (
          <span className={`agent-chip ${chip.status}`} title={chip.evaluation || ''}>
            agent: {chip.status}
          </span>
        )}
        <div className="step-mark-group" role="group" aria-label={`Mark step ${index + 1}`}>
          {STEP_MARKS.map((sm) => (
            <button
              key={sm.key}
              type="button"
              className={`step-mark-btn ${sm.key} ${mark?.status === sm.key ? 'active' : ''}`}
              disabled={saving}
              onClick={() => handleClick(sm.key)}
            >
              {sm.label}
            </button>
          ))}
        </div>
      </div>

      {draft && (
        <div className="step-override">
          <label htmlFor={`override-${caseId}-${index}`}>Why override the AI assessment?</label>
          <input
            id={`override-${caseId}-${index}`}
            type="text"
            value={draft.note}
            placeholder="Explain the override"
            onChange={(e) => setDraft({ ...draft, note: e.target.value })}
          />
          <button
            type="button"
            className="btn btn-ghost"
            disabled={saving || !draft.note.trim()}
            onClick={handleSaveOverride}
          >
            Save
          </button>
        </div>
      )}

      {err && <span className="toast-error" role="alert">{err}</span>}

      {!draft && mark?.note && (
        <div className="step-mark-note">
          {mark.note}
          {mark.overrode ? ` (overrode agent: ${mark.agent_status})` : ''}
        </div>
      )}
    </div>
  )
}
