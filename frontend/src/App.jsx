// Console shell: rail (left) + stage (right).
//
// While no real runId is set, useRunState polls /fixtures/sample_run_state.json
// so the UI works before the backend exists. Press "Run plan" to POST /runs
// (proxied to server.py in dev) and follow the live run.

import { useEffect, useMemo, useState } from 'react'

import Rail from './components/Rail.jsx'
import StatStrip from './components/StatStrip.jsx'
import ExecutionTape from './components/ExecutionTape.jsx'
import StageFoot from './components/StageFoot.jsx'
import ManualView from './components/ManualView.jsx'
import StartPanel from './components/StartPanel.jsx'
import CredentialsRow from './components/CredentialsRow.jsx'
import {
  logFailuresToJira,
  pushRunToQmetry,
  requestReport,
  startRun,
  stopAll,
  useRunState,
} from './hooks/useRunState.js'
import {
  saveGlobalCredentials,
  saveGlobalTargetUrl,
  useManualState,
} from './hooks/useManualState.js'

export default function App() {
  const [runId, setRunId] = useState(null)
  const { state, error } = useRunState(runId)
  const [activeId, setActiveId] = useState(null)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [stopMsg, setStopMsg] = useState('')
  // Which cases the next Live run covers. All-ticked by default so pressing
  // Run without touching anything behaves exactly as it did before tickboxes.
  const [selectedIds, setSelectedIds] = useState(() => new Set())
  const [tab, setTab] = useState('manual') // 'manual' | 'live'
  const [runUser, setRunUser] = useState('')
  const [runPw, setRunPw] = useState('')

  // What the rail browses: 'tr' = test runs, 'tc' = the project test case
  // library. A TR is opened as a plan of its own; a library case is opened as
  // the synthetic one-case plan "TC:<key>" (see agent/qmetry.py), which has no
  // QMetry execution behind it and so is never pushed back.
  const [mode, setMode] = useState('tr')

  // Deep links: ?tc=<case key> for a library case, ?cycle=<idOrKey> for a run.
  const initialParams =
    typeof window !== 'undefined'
      ? new URLSearchParams(window.location.search)
      : new URLSearchParams()
  const initialPlan = initialParams.get('tc')
    ? `TC:${initialParams.get('tc')}`
    : initialParams.get('cycle')

  const [chosenPlan, setChosenPlan] = useState(initialPlan)
  useEffect(() => {
    if (initialPlan?.startsWith('TC:')) setMode('tc')
    // Deep-link only — later mode changes are the tester's.
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const [defaultCycle, setDefaultCycle] = useState(null)
  // default_url + target_url feed the rail's GLOBAL URL control
  // (see RailSettings.jsx) — same /config call, no extra request. target_url
  // is the console-wide current value (server-side, not per case).
  const [defaultUrl, setDefaultUrl] = useState(null)
  const [targetUrl, setTargetUrl] = useState('')
  const [savingTargetUrl, setSavingTargetUrl] = useState(false)
  const [targetUrlMsg, setTargetUrlMsg] = useState(null)
  // login_username + has_password feed the rail's GLOBAL "Login as" control
  // (see RailSettings.jsx) — the console-wide default agent login, overridden
  // per case by the Manual card's own CredentialsRow and falling back itself
  // to the `.env` admin account.
  const [globalUsername, setGlobalUsername] = useState('')
  const [globalPassword, setGlobalPassword] = useState('')
  const [hasGlobalPassword, setHasGlobalPassword] = useState(false)
  const [savingGlobalCredentials, setSavingGlobalCredentials] = useState(false)
  const [globalCredentialsMsg, setGlobalCredentialsMsg] = useState(null)
  useEffect(() => {
    fetch('/config', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => {
        setDefaultCycle(c?.default_cycle ?? null)
        setDefaultUrl(c?.default_url ?? null)
        setTargetUrl(c?.target_url ?? '')
        setGlobalUsername(c?.login_username ?? '')
        setHasGlobalPassword(Boolean(c?.has_password))
      })
      .catch(() => {})
  }, [])

  async function handleSaveTargetUrl() {
    setTargetUrlMsg(null)
    setSavingTargetUrl(true)
    try {
      await saveGlobalTargetUrl(targetUrl)
      setTargetUrlMsg(targetUrl ? 'saved' : 'cleared — using default server')
    } catch (e) {
      setTargetUrlMsg(e.message)
    } finally {
      setSavingTargetUrl(false)
    }
  }

  async function handleSaveGlobalCredentials() {
    setGlobalCredentialsMsg(null)
    setSavingGlobalCredentials(true)
    try {
      const res = await saveGlobalCredentials(globalUsername, globalPassword)
      setHasGlobalPassword(Boolean(res?.has_password))
      setGlobalPassword('')
      setGlobalCredentialsMsg(
        globalUsername || globalPassword ? 'saved' : 'cleared — using .env account',
      )
    } catch (e) {
      setGlobalCredentialsMsg(e.message)
    } finally {
      setSavingGlobalCredentials(false)
    }
  }

  function selectPlan(planKeyToOpen) {
    if (planKeyToOpen !== chosenPlan) {
      // A different plan/case is now selected: the Live tab's run and active
      // case belonged to the previous plan, so drop them. Otherwise the stage
      // keeps showing the finished run for the case the tester just left.
      setRunId(null)
      setActiveId(null)
    }
    setChosenPlan(planKeyToOpen)
    setManualActiveId(null)
    // Keep the URL shareable without a reload.
    const url = new URL(window.location.href)
    url.searchParams.delete('cycle')
    url.searchParams.delete('tc')
    if (planKeyToOpen?.startsWith('TC:')) {
      url.searchParams.set('tc', planKeyToOpen.slice(3))
    } else if (planKeyToOpen) {
      url.searchParams.set('cycle', planKeyToOpen)
    }
    window.history.replaceState(null, '', url)
  }

  // A row in the rail browser: a run drills the rail in, a library case just
  // opens in the stage and leaves the list up.
  function handlePick(item) {
    selectPlan(mode === 'tc' ? item.plan_key : item.id)
  }

  function handleBack() {
    selectPlan(null)
  }

  // The tester must explicitly pick something — no auto-load from
  // QMETRY_DEFAULT_CYCLE or the demo fixture. Null here means "show the clean
  // start panel," not "not ready yet."
  const manualPlanKey = chosenPlan || null
  // Fallback kept for the Live-run plan label after a run starts.
  const planKey = chosenPlan || defaultCycle || state?.plan?.key || 'SOUSCLOUD-TP-45'
  const isStandalone = Boolean(chosenPlan?.startsWith('TC:'))

  // Manual session (the real QMetry cycle), shared by the rail + the manual stage.
  const {
    state: manualState,
    error: manualError,
    loading: manualLoading,
    refresh: refreshManual,
  } = useManualState(manualPlanKey)
  const [manualActiveId, setManualActiveId] = useState(null)

  // Live tab: before a run starts (runId null), preview the REAL cycle's cases
  // as queued instead of the demo fixture. Once a run is started, show it live.
  const livePreview = useMemo(
    () =>
      manualState
        ? {
            plan: manualState.plan,
            status: 'idle',
            elapsed_seconds: 0,
            summary: { total: manualState.summary.total, passed: 0, failed: 0, blocked: 0 },
            test_cases: (manualState.cases ?? []).map((c) => ({
              id: c.id,
              name: c.name,
              status: 'queued',
              // The case's last QMetry verdict, for the rail's status dot.
              // Rides along on the manual session — no extra request, and
              // run_state is deliberately untouched.
              execution_result: c.execution_result ?? null,
              steps: [],
            })),
          }
        : null,
    [manualState],
  )
  const liveState = runId ? state : livePreview ?? state

  // Tick every case whenever a different plan's cases load. Keyed on the plan
  // AND the id list so switching runs never inherits a stale selection, while
  // live status updates (which don't change the id list) leave ticks alone.
  const previewCaseIds = (livePreview?.test_cases ?? []).map((c) => c.id).join('|')
  useEffect(() => {
    const ids = (livePreview?.test_cases ?? []).map((c) => c.id)
    if (ids.length) setSelectedIds(new Set(ids))
  }, [previewCaseIds])

  function toggleCase(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAllCases(on) {
    setSelectedIds(on ? new Set((livePreview?.test_cases ?? []).map((c) => c.id)) : new Set())
  }

  // Auto-focus the currently-running case; otherwise keep whatever the
  // tester picked. First time we see cases, default to the first one.
  useEffect(() => {
    if (!liveState?.test_cases?.length) return
    if (activeId && liveState.test_cases.find((c) => c.id === activeId)) return
    const running = liveState.test_cases.find((c) => c.status === 'running')
    setActiveId((running ?? liveState.test_cases[0]).id)
  }, [liveState, activeId])

  // Default the manual selection to the first case in the cycle.
  useEffect(() => {
    if (!manualState?.cases?.length) return
    if (manualActiveId && manualState.cases.find((c) => c.id === manualActiveId)) return
    setManualActiveId(manualState.cases[0].id)
  }, [manualState, manualActiveId])

  // The rail mirrors whichever view is active: the QMetry cycle on the Manual
  // tab, the live run on the Live tab.
  // On the Live tab the rail lists the WHOLE cycle and overlays the statuses of
  // whichever cases the run covered. It cannot just mirror liveState: once a
  // run starts, that holds only the SELECTED cases, so a deselected case would
  // vanish from the rail and could never be re-ticked — which would break the
  // resume-a-stopped-run workflow the tickboxes exist for. The tape stays the
  // view of the run; the rail stays the view of the plan.
  const liveRailState = useMemo(() => {
    if (!livePreview) return liveState
    const ran = new Map((state?.test_cases ?? []).map((c) => [c.id, c]))
    return {
      plan: liveState?.plan ?? livePreview.plan,
      summary: liveState?.summary ?? livePreview.summary,
      test_cases: livePreview.test_cases.map((c) => ({
        ...c,
        status: runId ? (ran.get(c.id)?.status ?? 'queued') : c.status,
      })),  // `...c` keeps execution_result, so a case this run did not cover
            // still shows its QMetry verdict rather than a bare dashed dot.
    }
  }, [livePreview, liveState, state, runId])

  const railState =
    tab === 'manual'
      ? manualState && {
          plan: manualState.plan,
          summary: manualState.summary,
          test_cases: (manualState.cases ?? []).map((c) => ({
            id: c.id,
            name: c.name,
            status: c.manual.status === 'unmarked' ? 'queued' : c.manual.status,
          })),
        }
      : liveRailState
  const railActiveId = tab === 'manual' ? manualActiveId : activeId
  const railSelect = tab === 'manual' ? setManualActiveId : setActiveId
  // The rail drills into a run's case list; a library case keeps the browser up
  // so the tester can move to the next one without going back first.
  const drilledIn = Boolean(manualPlanKey) && !isStandalone
  // The human key the server resolved for the plan (SOUSCLOUD-TR-482 /
  // SOUSCLOUD-TC-2), not the internal cycle id the URL carries.
  const planLabel = manualState?.plan?.key ?? manualPlanKey

  const activeCase = useMemo(
    () => liveState?.test_cases?.find((c) => c.id === activeId) ?? null,
    [liveState, activeId],
  )

  const isRunning = liveState?.status === 'running'
  const isDone = liveState?.status === 'done'
  // Global rail settings (URL + Login as) disable while ANY run (Live tab, or
  // a Manual-tab per-case agent run) is in flight — either could be
  // mid-navigation, or mid-login, against the values they name.
  const manualAgentRunning = manualState?.cases?.some((c) => c.manual.agent_status === 'running')
  // One concept, two consumers: the rail settings disable while anything is in
  // flight, and the emergency stop enables on exactly the same condition.
  const anythingRunning = isRunning || Boolean(manualAgentRunning)
  const railSettingsDisabled = anythingRunning

  // Emergency stop. Cancels every in-flight run in one press, whichever tab
  // started it. No optimistic local state: the existing pollers pick up the
  // cancelled status, so the server's view of what is running stays the only
  // source of truth.
  async function handleStopAll() {
    setStopping(true)
    setStopMsg('')
    try {
      const { cancelled } = await stopAll()
      const n = cancelled?.length ?? 0
      setStopMsg(n ? `Stopped ${n} run${n === 1 ? '' : 's'}` : 'Nothing was running')
      refreshManual?.()
    } catch (e) {
      console.error('stopAll failed:', e)
      setStopMsg('Could not reach the backend')
    } finally {
      setStopping(false)
    }
  }

  async function handleRun() {
    if (!planKey) return
    setStarting(true)
    try {
      // Run the real QMetry cycle currently shown (?cycle=…), not the fixture plan.
      // Send the selection only when it is a real subset — omitting the field
      // keeps the "run everything" contract the CLI and older callers rely on.
      const allCaseIds = (livePreview?.test_cases ?? []).map((c) => c.id)
      const picked = allCaseIds.filter((id) => selectedIds.has(id))
      const { run_id } = await startRun(planKey, {
        username: runUser,
        password: runPw,
        caseIds: picked.length && picked.length < allCaseIds.length ? picked : null,
      })
      setRunId(run_id)
    } catch (e) {
      // Backend not up yet during scaffold — keep showing the fixture and surface the error.
      console.error('startRun failed:', e)
      alert(
        'Could not reach the agent backend. Start `python server.py` then try again.',
      )
    } finally {
      setStarting(false)
    }
  }

  async function handleReport() {
    if (!runId) return
    try {
      const { path } = await requestReport(runId)
      window.open(path, '_blank', 'noopener')
    } catch (e) {
      console.error(e)
    }
  }

  async function handleLogBugs() {
    if (!runId) return
    if (!confirm('Create Jira bugs for every failed case in this run?')) return
    try {
      await logFailuresToJira(runId)
    } catch (e) {
      console.error(e)
    }
  }

  const totalCases = livePreview?.test_cases?.length ?? 0
  const pickedCount = (livePreview?.test_cases ?? []).filter((c) => selectedIds.has(c.id)).length
  const partialSelection = totalCases > 0 && pickedCount < totalCases
  const runLabel = isRunning
    ? '⏸ Running…'
    : partialSelection
      ? `▶ Run ${pickedCount} of ${totalCases}`
      : isDone
        ? '▶ Run again'
        : '▶ Run plan'

  return (
    <div className="app">
      <Rail
        state={railState}
        activeId={railActiveId}
        onSelectCase={railSelect}
        mode={mode}
        onModeChange={setMode}
        onPick={handlePick}
        drilledIn={drilledIn}
        onBack={handleBack}
        browseActiveKey={isStandalone ? chosenPlan.slice(3) : null}
        targetUrl={targetUrl}
        defaultUrl={defaultUrl}
        onTargetUrlChange={setTargetUrl}
        onSaveTargetUrl={handleSaveTargetUrl}
        savingTargetUrl={savingTargetUrl}
        targetUrlMsg={targetUrlMsg}
        settingsDisabled={railSettingsDisabled}
        globalUsername={globalUsername}
        globalPassword={globalPassword}
        onGlobalUsernameChange={setGlobalUsername}
        onGlobalPasswordChange={setGlobalPassword}
        hasGlobalPassword={hasGlobalPassword}
        onSaveGlobalCredentials={handleSaveGlobalCredentials}
        savingGlobalCredentials={savingGlobalCredentials}
        globalCredentialsMsg={globalCredentialsMsg}
        anythingRunning={anythingRunning}
        onStopAll={handleStopAll}
        stopping={stopping}
        stopMsg={stopMsg}
        selectable={tab === 'live'}
        selectedIds={selectedIds}
        onToggleCase={toggleCase}
        onToggleAll={toggleAllCases}
      />
      <main className="stage">
        <nav className="view-tabs" role="tablist" aria-label="Console view">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'manual'}
            className={`view-tab ${tab === 'manual' ? 'active' : ''}`}
            onClick={() => setTab('manual')}
          >
            Manual
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'live'}
            className={`view-tab ${tab === 'live' ? 'active' : ''}`}
            onClick={() => setTab('live')}
          >
            Live run
          </button>
        </nav>

        {!manualPlanKey ? (
          <StartPanel defaultCycle={defaultCycle} onSelectCycle={selectPlan} />
        ) : tab === 'live' ? (
          <>
            <header className="stage-head">
              <span className="stage-head-id">{activeCase?.id ?? liveState?.plan?.key ?? '—'}</span>
              <h1 className="stage-head-title">
                {activeCase?.name ?? liveState?.plan?.name ?? 'QA Agent Console'}
              </h1>
              <button
                type="button"
                className={`btn btn-primary ${isRunning ? 'running' : ''}`}
                disabled={isRunning || starting || (totalCases > 0 && pickedCount === 0)}
                onClick={handleRun}
                title={
                  totalCases > 0 && pickedCount === 0
                    ? 'Tick at least one test case in the rail'
                    : undefined
                }
              >
                {runLabel}
              </button>
              <CredentialsRow
                username={runUser}
                password={runPw}
                onUsernameChange={setRunUser}
                onPasswordChange={setRunPw}
                disabled={isRunning}
                helpText="Leave blank to use the rail's global login (or the .env admin if that's blank too). A case with its own login saved on the Manual tab overrides both."
              />
            </header>
            <StatStrip state={liveState} />
            <ExecutionTape activeCase={activeCase} />
            <StageFoot
              state={liveState}
              activeCase={activeCase}
              onReport={handleReport}
              onLogBugs={handleLogBugs}
              onPushQmetry={runId ? (mode) => pushRunToQmetry(runId, mode) : undefined}
            />
          </>
        ) : (
          <ManualView
            plan={manualPlanKey}
            planLabel={planLabel}
            state={manualState}
            error={manualError}
            loading={manualLoading}
            refresh={refreshManual}
            activeId={manualActiveId}
          />
        )}

        {error && (
          <div role="alert" className="toast-error">{error}</div>
        )}
      </main>
    </div>
  )
}
