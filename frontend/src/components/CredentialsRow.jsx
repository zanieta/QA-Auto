// The shared "Login as" row. Used by the Manual case card (per case) and the
// Live stage head (per run). Purely presentational: the parent owns the values
// and decides what saving means.

export default function CredentialsRow({
  username,
  password,
  onUsernameChange,
  onPasswordChange,
  disabled = false,
  savedPassword = false,
  helpText,
  children,
}) {
  return (
    <>
      <div className="manual-credentials">
        <span className="manual-credentials-label">Login as</span>
        <input
          type="text"
          placeholder="username (default admin)"
          value={username}
          onChange={(e) => onUsernameChange(e.target.value)}
          disabled={disabled}
          aria-label="Login username"
        />
        <input
          type="password"
          placeholder={savedPassword ? '••• saved' : 'password'}
          value={password}
          onChange={(e) => onPasswordChange(e.target.value)}
          disabled={disabled}
          aria-label="Login password"
        />
        {children}
      </div>
      {helpText && <p className="manual-credentials-help">{helpText}</p>}
    </>
  )
}
