import { useRef, useState } from 'react'

// The per-case "Server" row: a known-environment dropdown plus a free-text
// address bar, sitting beside the "Login as" credentials row on the Manual case
// card. Purely presentational: the parent owns the value and decides what saving
// means (see CredentialsRow.jsx, which this mirrors).
//
// Mount this with key={testCase.id} — the one piece of local state below
// (newLinkIntent) is per-case and is meant to be discarded when the tester
// switches cases, which remounting does for free.

export default function TargetUrlRow({
  url,
  environments = [],
  defaultUrl,
  onUrlChange,
  disabled = false,
  children,
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
    <>
      <div className="manual-credentials manual-targeturl">
        <span className="manual-credentials-label">Server</span>
        <select
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
          className="mono"
          placeholder={isNewLink ? 'https://…' : defaultUrl || 'https://…'}
          value={url}
          onChange={(e) => onUrlChange(e.target.value)}
          disabled={disabled}
          aria-label="Server address"
        />
        {children}
      </div>
      <p className="manual-credentials-help">
        {defaultUrl
          ? `Any URL works — type one in, or leave blank to use the default server (${defaultUrl}).`
          : 'Any URL works — type one in, or leave blank to use the configured default server.'}
      </p>
      {nonDefault && (
        <p className="manual-targeturl-warn" role="alert">
          ⚠ Non-test server — the agent will click Save/Delete against live data.
        </p>
      )}
    </>
  )
}
