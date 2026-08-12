// frontend/src/components/ManualCase.jsx
// One test case: read-only steps each showing their test data and the agent's
// verdict, an overall notes field, and an optional inline agent run.
//
// There are deliberately NO per-step Pass/Fail/Blocked/Skip buttons — the
// agent's verdict is the indication that matters, so each step shows only an
// `agent: pass` / `agent: fail` chip. The case's status pill follows that
// verdict (set server-side in ManualStore.set_agent).

import { useEffect, useRef, useState } from 'react'

import Step from './Step.jsx'
import CredentialsRow from './CredentialsRow.jsx'
import { cancelRun, markCase, runAgentCase, saveCaseCredentials } from '../hooks/useManualState.js'
import { useRunState } from '../hooks/useRunState.js'

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

      {/* The case's own test data — QMetry surfaces a parameterised case's
          parameter table as "Test Data". These are the values that fill the
          `[~id]` placeholders in the steps below, so showing them here is what
          makes a shared step ("log in as X") legible for THIS case. Absent on
          most cases, so the block is omitted rather than showing "none" —
          unlike the per-step field, where a missing label would be ambiguous. */}
      {testCase.test_data?.length > 0 && (
        <div className="manual-casedata">
          <div className="manual-casedata-label">Test data</div>
          <dl className="manual-casedata-list">
            {testCase.test_data.map((p) => (
              <div key={p.name} className="manual-casedata-row">
                <dt>{p.name}</dt>
                <dd className="mono">{p.value || <em>empty</em>}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      <CredentialsRow
        username={loginUser}
        password={loginPw}
        onUsernameChange={setLoginUser}
        onPasswordChange={setLoginPw}
        disabled={agentRunning}
        savedPassword={m.has_password}
        helpText="Leave blank to use the system admin account."
      >
        <button
          type="button"
          className="btn btn-ghost"
          disabled={agentRunning}
          onClick={handleSaveCredentials}
        >
          Save
        </button>
        {credsMsg && <span className="manual-credentials-msg">{credsMsg}</span>}
      </CredentialsRow>

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
                {/* Shown on every step, "none" included: a blank field would
                    leave the tester guessing whether there's nothing to enter
                    or the data just didn't load. */}
                <div className="manual-step-data">
                  <span className="manual-step-data-label">Test data</span>
                  {s.test_data ? (
                    <span className="manual-step-data-value">{cleanMarkup(s.test_data)}</span>
                  ) : (
                    <span className="manual-step-data-none">none</span>
                  )}
                </div>
                <AgentVerdict chip={chipByStep[i]} mark={stepMark} />
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

      {/* The agent-notes panel is deliberately not rendered. Every verdict it
          restated is already on its own step, so it was a long duplicate wall of
          text. `agent_note` is still recorded server-side and still goes into
          the QMetry comment on push — only the on-screen block is gone. */}

      {agentRunId && (
        <AgentTape key={agentRunId} state={agentState} caseId={testCase.id} onDone={onChanged} />
      )}
    </section>
  )
}

// The agent's verdict for one step, read-only. `chip` is the live tape result;
// `mark.agent_status` is what a previous run recorded, so the verdict survives a
// refresh once the run's state is gone.
function AgentVerdict({ chip, mark }) {
  const status = chip && chip.status !== 'running' ? chip.status : mark?.agent_status
  if (!status) return null
  const reason = chip?.evaluation || ''
  return (
    <div className="step-verdict">
      <span className={`agent-chip ${status}`}>agent: {status}</span>
      {reason && <span className="step-verdict-reason">{reason}</span>}
    </div>
  )
}

function AgentTape({ state, caseId, onDone }) {
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
      {/* Just the tape. Each step already carries the agent's verdict in the
          steps list above — repeating a mark row here was duplicate UI. */}
      {steps.map((s, i) => (
        <div key={i} className="tape-entry">
          <Step step={s} />
        </div>
      ))}
      {steps.length === 0 && <div className="manual-step-expected">Agent is starting…</div>}
    </div>
  )
}
