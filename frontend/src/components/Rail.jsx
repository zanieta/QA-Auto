// Left rail. Two states:
//
//   browse     — the TR|TC picker (CaseBrowser). Nothing is open yet, or a
//                library test case is open (TC mode keeps the list in view so
//                the tester can walk down it).
//   drilled in — a test run is open: back link, run meta with progress, and
//                that run's cases. The active case row gets a translucent
//                white background.

import CaseBrowser from './CaseBrowser.jsx'
import RailSettings from './RailSettings.jsx'

export default function Rail({
  state,
  activeId,
  onSelectCase,
  mode,
  onModeChange,
  onPick,
  drilledIn,
  onBack,
  browseActiveKey,
  targetUrl,
  defaultUrl,
  onTargetUrlChange,
  onSaveTargetUrl,
  savingTargetUrl,
  targetUrlMsg,
  settingsDisabled,
  globalUsername,
  globalPassword,
  onGlobalUsernameChange,
  onGlobalPasswordChange,
  hasGlobalPassword,
  onSaveGlobalCredentials,
  savingGlobalCredentials,
  globalCredentialsMsg,
  anythingRunning,
  onStopAll,
  stopping,
  stopMsg,
  selectable,
  selectedIds,
  onToggleCase,
  onToggleAll,
}) {
  const summary = state?.summary ?? { total: 0, passed: 0, failed: 0, blocked: 0 }
  const cases = state?.test_cases ?? []
  const done = summary.passed + summary.failed + (summary.blocked ?? 0)
  const pct = summary.total ? Math.round((done / summary.total) * 100) : 0
  // Selection is Live-tab only; on the Manual tab `selectable` is false and
  // none of this renders (that tab has its own per-step agent checkboxes).
  const selectedCount = selectable
    ? cases.filter((c) => selectedIds?.has(c.id)).length
    : 0
  const allSelected = selectable && cases.length > 0 && selectedCount === cases.length

  return (
    <aside className="rail" aria-label="Plan navigation">
      <div className="rail-brand">
        <div className="rail-shield" aria-hidden="true">
          {/* Drop frontend/public/duke-logo.png to swap the placeholder. */}
          <img
            src="/duke-logo.png"
            alt=""
            onError={(e) => {
              e.currentTarget.style.display = 'none'
              e.currentTarget.parentElement.textContent = 'Duke'
            }}
          />
        </div>
        <div>
          <div className="rail-title">QA Agent</div>
          <div className="rail-subtitle">Sous Chef Cloud</div>
        </div>
      </div>

      {/* GLOBAL, not per-case — one value for the whole console, visible and
          settable regardless of browse/drilled-in state or which tab (Manual
          vs Live run) is active, since the rail is one shared instance. */}
      <RailSettings
        url={targetUrl}
        defaultUrl={defaultUrl}
        onUrlChange={onTargetUrlChange}
        onSaveUrl={onSaveTargetUrl}
        savingUrl={savingTargetUrl}
        urlMsg={targetUrlMsg}
        username={globalUsername}
        password={globalPassword}
        onUsernameChange={onGlobalUsernameChange}
        onPasswordChange={onGlobalPasswordChange}
        savedPassword={hasGlobalPassword}
        onSaveCredentials={onSaveGlobalCredentials}
        savingCredentials={savingGlobalCredentials}
        credentialsMsg={globalCredentialsMsg}
        disabled={settingsDisabled}
      />

      {/* Emergency stop. Rendered outside the browse/drilled-in split on
          purpose so it is in the same place on both tabs and in both states —
          a brake you have to go looking for is not a brake. Disabled rather
          than hidden when idle, so its location is learned before it's needed. */}
      <button
        type="button"
        className="rail-stop"
        onClick={onStopAll}
        disabled={!anythingRunning || stopping}
        title={
          anythingRunning
            ? 'Cancel every run in progress'
            : 'Nothing is running'
        }
      >
        {stopping ? 'Stopping…' : '■ Stop everything'}
      </button>
      {stopMsg && <div className="rail-stop-msg">{stopMsg}</div>}

      {!drilledIn ? (
        <CaseBrowser
          mode={mode}
          onModeChange={onModeChange}
          activeKey={browseActiveKey}
          onPick={onPick}
        />
      ) : (
        <>
          <button type="button" className="rail-back" onClick={onBack}>
            ← All test runs
          </button>

          <div className="rail-plan">
            <div className="rail-plan-key mono">{state?.plan?.key ?? '—'}</div>
            <div className="rail-plan-name">{state?.plan?.name ?? 'No plan selected'}</div>
            <div className="rail-progress">
              <div className="rail-progress-bar">
                <div className="rail-progress-fill" style={{ width: `${pct}%` }} />
              </div>
              <div className="rail-progress-text">
                <span>
                  {done} / {summary.total}
                </span>
                <span>{pct}%</span>
              </div>
            </div>
          </div>

          <div className="rail-section-label">
            <span>Test cases</span>
            {selectable && cases.length > 0 && (
              <span className="rail-select-meta">
                <span className="mono">
                  {selectedCount} of {cases.length}
                </span>
                <button
                  type="button"
                  className="rail-select-all"
                  onClick={() => onToggleAll?.(!allSelected)}
                  disabled={anythingRunning}
                >
                  {allSelected ? 'None' : 'All'}
                </button>
              </span>
            )}
          </div>
          <div className="rail-cases" role="list">
            {cases.length === 0 && (
              <div className="browser-msg">Loading test cases…</div>
            )}
            {cases.map((c) => {
              const picked = !selectable || selectedIds?.has(c.id)
              const { cls: statusCls, label: statusLabel } = caseStatus(
                c.status,
                c.execution_result,
              )
              const row = (
                <button
                  type="button"
                  className={`case-row ${activeId === c.id ? 'active' : ''}`}
                  onClick={() => onSelectCase?.(c.id)}
                  // The stripe is colour-only, so the status has to reach a
                  // screen reader (and anyone who cannot separate the hues)
                  // some other way — it rides on the row's own label.
                  title={`${c.id} — ${c.name} · ${statusLabel}`}
                  aria-label={`${c.id} ${c.name}. ${statusLabel}`}
                >
                  <span className="case-row-id">{c.id}</span>
                  <span className="case-row-name">{c.name}</span>
                </button>
              )
              // A checkbox cannot nest inside a <button> — invalid HTML that
              // breaks both the click target and keyboard focus. So when the
              // list is selectable the row becomes a flex wrapper holding a
              // real checkbox BESIDE the button, rather than inside it.
              if (!selectable) {
                return (
                  <div
                    key={c.id}
                    role="listitem"
                    className={`case-row-wrap ${statusCls}`}
                  >
                    {row}
                  </div>
                )
              }
              return (
                <div
                  key={c.id}
                  role="listitem"
                  className={`case-row-wrap ${statusCls}${picked ? '' : ' unpicked'}`}
                >
                  <input
                    type="checkbox"
                    className="case-pick"
                    checked={Boolean(picked)}
                    onChange={() => onToggleCase?.(c.id)}
                    disabled={anythingRunning}
                    aria-label={`Run ${c.id}`}
                  />
                  {row}
                </div>
              )
            })}
          </div>
        </>
      )}
    </aside>
  )
}

// QMetry result name -> the stripe class for that verdict. Mapped by NAME,
// not by the hex the API returns per case: the colour belongs in the token
// system, and a QMetry admin's config must not be able to repaint the console.
// "Not Executed" is absent on purpose — it falls through to the `queued` look
// (a faint hairline), because a never-run case and a not-yet-run case read the
// same to a tester about to press Run.
const QMETRY_STRIPE = {
  Pass: 'qm-pass',
  Fail: 'qm-fail',
  Blocked: 'qm-blocked',
  'Work In Progress': 'qm-wip',
}

// A case's status as a row class + a spoken label. The class drives the 4px
// left-edge stripe (see .case-row-wrap in tokens.css); the label is the only
// non-visual carrier of the status, since the stripe is pure colour.
//
// Live run status ALWAYS wins once a run has touched this case: what is
// happening now outranks what QMetry recorded last time. Only a still-queued
// case falls back to the QMetry verdict.
function caseStatus(status, qmetry) {
  if (status === 'queued' && qmetry) {
    const cls = QMETRY_STRIPE[qmetry.name]
    if (cls) return { cls, label: `Last QMetry result: ${qmetry.name}` }
    // A known-but-unmapped verdict (today only "Not Executed") still says so
    // out loud, even though it looks identical to a queued case.
    return { cls: 'queued', label: `Last QMetry result: ${qmetry.name}` }
  }
  return { cls: status || 'queued', label: status || 'queued' }
}
