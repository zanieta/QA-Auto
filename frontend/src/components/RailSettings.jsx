// GLOBAL rail settings — two console-wide controls, shared by every test run
// and every case on both the Manual and Live-run tabs (the rail is one shared
// instance, which is why this is their home rather than a per-case widget):
//
//   URL       — the target server. Always-editable DM Mono address bar +
//               Save + amber warning when it differs from the default.
//   Login as  — the global default agent login. Same "Login as" control used
//               on the Manual case card (`CredentialsRow`), restyled for the
//               rail's navy surface. The per-case row on the Manual card
//               overrides this for that one case; this overrides `.env`.
//
// Both replace the old dropdown-driven ServerPicker (removed 2026-08-19): once
// there is no environment shortcut list, there is nothing for a derived
// selection to keep in sync, so the URL control is now a plain box.
//
// Purely presentational: the parent (App.jsx) owns all values and decides
// what saving means.

import CredentialsRow from './CredentialsRow.jsx'

export default function RailSettings({
  url,
  defaultUrl,
  onUrlChange,
  onSaveUrl,
  savingUrl = false,
  urlMsg = null,
  username,
  password,
  onUsernameChange,
  onPasswordChange,
  savedPassword = false,
  onSaveCredentials,
  savingCredentials = false,
  credentialsMsg = null,
  disabled = false,
}) {
  const effectiveUrl = url || defaultUrl || ''
  const nonDefault = Boolean(defaultUrl) && effectiveUrl !== defaultUrl

  return (
    <div className="rail-settings">
      <div className="rail-settings-block">
        <div className="rail-settings-label">URL</div>
        <input
          type="text"
          className="rail-settings-input mono"
          placeholder={defaultUrl || 'https://…'}
          value={url}
          onChange={(e) => onUrlChange(e.target.value)}
          disabled={disabled}
          aria-label="Server address"
        />
        <div className="rail-settings-actions">
          <button
            type="button"
            className="rail-settings-save"
            disabled={disabled || savingUrl}
            onClick={onSaveUrl}
          >
            {savingUrl ? 'Saving…' : 'Save'}
          </button>
          {urlMsg && <span className="rail-settings-msg">{urlMsg}</span>}
        </div>
        <p className="rail-settings-help">
          {defaultUrl
            ? `Blank uses the default server (${defaultUrl}).`
            : 'Blank uses the configured default server.'}
        </p>
        {nonDefault && (
          <p className="rail-settings-warn" role="alert">
            ⚠ Non-test server — the agent will click Save/Delete against live data.
          </p>
        )}
      </div>

      <div className="rail-settings-block">
        <CredentialsRow
          username={username}
          password={password}
          onUsernameChange={onUsernameChange}
          onPasswordChange={onPasswordChange}
          disabled={disabled}
          savedPassword={savedPassword}
          helpText="Global default login for every run. A case's own saved login (Manual tab) overrides this; blank falls back to the .env admin account."
        >
          <button
            type="button"
            className="rail-settings-save"
            disabled={disabled || savingCredentials}
            onClick={onSaveCredentials}
          >
            {savingCredentials ? 'Saving…' : 'Save'}
          </button>
          {credentialsMsg && <span className="rail-settings-msg">{credentialsMsg}</span>}
        </CredentialsRow>
      </div>
    </div>
  )
}
