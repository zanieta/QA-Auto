import { useRef, useState } from 'react'

// GLOBAL "Server" control — lives in the left rail (Rail.jsx), above the
// BROWSE picker, and is shared by both the Manual and Live-run tabs (the rail
// is one instance either way). One value for the whole console: read from
// GET /config's `target_url` on load, saved via `saveGlobalTargetUrl` in
// useManualState.js (POST /settings/target-url). This used to be a per-case
// row on the Manual card (`TargetUrlRow.jsx`, removed 2026-08-19) — the
// interaction design below is unchanged from that component, only the mount
// point and the vertical layout (narrow rail column) are new.
//
// Purely presentational: the parent (App.jsx) owns `url` and `onSave`.

export default function ServerPicker({
  url,
  environments = [],
  defaultUrl,
  onUrlChange,
  onSave,
  saving = false,
  msg = null,
  disabled = false,
}) {
  // Selection is DERIVED from the url, so the dropdown and the box can never
  // drift apart: "" is the default, an exact match is that environment,
  // anything else is a new link. The one exception is newLinkIntent — the
  // tester picked "New link…" and hasn't typed yet, so the box is empty but
  // must NOT snap the dropdown back to Default underneath them.
  const [newLinkIntent, setNewLinkIntent] = useState(false)
  const inputRef = useRef(null)

  const matched = environments.find((env) => env.url === url)
  const isNewLink = newLinkIntent || Boolean(url && !matched)
  const selection = isNewLink ? 'new' : url ? matched.name : 'default'
  const effectiveUrl = url || defaultUrl || ''
  const nonDefault = Boolean(defaultUrl) && effectiveUrl !== defaultUrl

  function handleSelect(e) {
    const value = e.target.value
    if (value === 'new') {
      // Clear to an empty box and put the cursor in it — picking "New link…"
      // should leave the tester typing, not staring at the old URL.
      setNewLinkIntent(true)
      onUrlChange('')
      inputRef.current?.focus()
      return
    }
    setNewLinkIntent(false)
    if (value === 'default') {
      onUrlChange('')
    } else {
      const env = environments.find((x) => x.name === value)
      if (env) onUrlChange(env.url)
    }
  }

  return (
    <div className="rail-server">
      <div className="rail-server-label">Server</div>
      <select
        className="rail-server-select"
        value={selection}
        onChange={handleSelect}
        disabled={disabled}
        aria-label="Server environment"
      >
        <option value="default">Default</option>
        {environments.map((env) => (
          <option key={env.name} value={env.name}>
            {env.name}
          </option>
        ))}
        <option value="new">New link…</option>
      </select>
      <input
        ref={inputRef}
        type="text"
        className="rail-server-input mono"
        placeholder={isNewLink ? 'https://…' : defaultUrl || 'https://…'}
        value={url}
        onChange={(e) => onUrlChange(e.target.value)}
        disabled={disabled}
        aria-label="Server address"
      />
      <div className="rail-server-actions">
        <button
          type="button"
          className="rail-server-save"
          disabled={disabled || saving}
          onClick={onSave}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        {msg && <span className="rail-server-msg">{msg}</span>}
      </div>
      <p className="rail-server-help">
        {defaultUrl
          ? `Blank uses the default server (${defaultUrl}).`
          : 'Blank uses the configured default server.'}
      </p>
      {nonDefault && (
        <p className="rail-server-warn" role="alert">
          ⚠ Non-test server — the agent will click Save/Delete against live data.
        </p>
      )}
    </div>
  )
}
