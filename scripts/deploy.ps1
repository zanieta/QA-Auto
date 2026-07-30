<#
.SYNOPSIS
    Idempotent setup + launch for the Sous Chef QA console (local deploy package).

.DESCRIPTION
    Run via deploy.cmd (double-click) or directly:
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy.ps1 [flags]

    Steps (each skips when already satisfied):
      1. Check Python >= 3.11 is on PATH (python or py -3).
      2. Create .venv if missing.
      3. Install/upgrade pip deps (truststore-based TLS, no --trusted-host).
      4. Install Playwright Chromium (best-effort corporate CA export first).
      5. Ensure .env exists (copy from .env.example + halt if it had to be created).
      6. Launch server.py, wait for /config, open the browser, then block until
         the tester closes the server window.

.PARAMETER Reinstall
    Force a fresh pip install and Playwright Chromium reinstall even if the
    sentinel files say they're already done.

.PARAMETER NoBrowser
    Skip auto-opening the default browser after the server comes up.

.PARAMETER SetupOnly
    Do steps 1-5 (Python/venv/deps/chromium/.env) and then exit 0 without
    starting the server.
#>
[CmdletBinding()]
param(
    [switch]$Reinstall,
    [switch]$NoBrowser,
    [switch]$SetupOnly
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)
    Write-Host "   $Message"
}

# ---------------------------------------------------------------------------
# Step 1: Python check
# ---------------------------------------------------------------------------
Write-Step "Checking Python"

function Get-PythonCommand {
    # Returns @{ Exe = <exe>; Args = <string[]>; VersionString = <string> } or $null
    $candidates = @(
        @{ Exe = 'python'; Args = @() },
        @{ Exe = 'py'; Args = @('-3') }
    )
    foreach ($c in $candidates) {
        try {
            $verOutput = & $c.Exe @($c.Args) --version 2>&1 | Out-String
        } catch {
            continue
        }
        if ($LASTEXITCODE -ne 0 -and -not $verOutput) { continue }
        if ($verOutput -match 'Python\s+(\d+)\.(\d+)(\.(\d+))?') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                return @{ Exe = $c.Exe; Args = $c.Args; VersionString = $verOutput.Trim() }
            } else {
                Write-Info "Found $($verOutput.Trim()) via '$($c.Exe)' - too old (need >= 3.11)."
            }
        }
    }
    return $null
}

$PyCmd = Get-PythonCommand
if (-not $PyCmd) {
    Write-Host ""
    Write-Host "Python 3.11 or newer was not found on PATH." -ForegroundColor Red
    Write-Host "Install it from: https://www.python.org/downloads/"
    Write-Host "IMPORTANT: check 'Add python.exe to PATH' during install, then re-run deploy.cmd."
    exit 1
}
Write-Info "Using $($PyCmd.VersionString) (via '$($PyCmd.Exe) $($PyCmd.Args -join ' ')')"

# ---------------------------------------------------------------------------
# Step 2: venv
# ---------------------------------------------------------------------------
Write-Step "Python virtual environment (.venv)"

$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path (Join-Path $RepoRoot '.venv'))) {
    Write-Info "Creating .venv ..."
    & $PyCmd.Exe @($PyCmd.Args) -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create .venv." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Info ".venv already exists - skipping creation."
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Expected venv Python at $VenvPython but it's missing. Aborting." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Corporate TLS trust — export the machine's trusted root CAs to a PEM bundle
# and point pip + Playwright at it, so installs verify correctly behind Duke's
# TLS inspection WITHOUT --trusted-host or disabling verification (repo rule).
# A fresh .venv has no pip.ini, so pip would otherwise fail
# CERTIFICATE_VERIFY_FAILED (certifi doesn't carry the corporate root CA).
# ---------------------------------------------------------------------------
Write-Step "Corporate TLS trust (root CA bundle)"

$CaBundle = Join-Path $PSScriptRoot '_corp-ca-bundle.pem'
try {
    $sb = [System.Text.StringBuilder]::new()
    Get-ChildItem Cert:\LocalMachine\Root | ForEach-Object {
        $b64 = [Convert]::ToBase64String($_.RawData, 'InsertLineBreaks')
        [void]$sb.AppendLine('-----BEGIN CERTIFICATE-----')
        [void]$sb.AppendLine($b64)
        [void]$sb.AppendLine('-----END CERTIFICATE-----')
    }
    Set-Content -Path $CaBundle -Value $sb.ToString() -Encoding ascii
    $env:NODE_EXTRA_CA_CERTS = $CaBundle   # Playwright browser download
    $env:PIP_CERT            = $CaBundle   # pip upgrade + install
    $env:SSL_CERT_FILE       = $CaBundle   # any other Python TLS during setup
    Write-Info "Exported trusted root CAs to $CaBundle"
} catch {
    Write-Warning "Could not export corporate CA bundle: $_"
    Write-Warning "Installs may fail on a TLS-inspected network; see DEPLOY.md troubleshooting."
}

# ---------------------------------------------------------------------------
# Step 3: pip dependencies
# ---------------------------------------------------------------------------
Write-Step "Python dependencies"

$RequirementsPath = Join-Path $RepoRoot 'requirements.txt'
$DepsSentinel = Join-Path $RepoRoot '.venv\.deps-installed'
$CurrentHash = (Get-FileHash -Path $RequirementsPath -Algorithm SHA256).Hash

$NeedInstall = $true
if ((-not $Reinstall) -and (Test-Path $DepsSentinel)) {
    $StoredHash = (Get-Content -Path $DepsSentinel -Raw).Trim()
    if ($StoredHash -eq $CurrentHash) {
        $NeedInstall = $false
    }
}

if ($NeedInstall) {
    Write-Info "Installing dependencies from requirements.txt ..."
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip upgrade failed." -ForegroundColor Red
        exit 1
    }
    & $VenvPython -m pip install -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Dependency install failed." -ForegroundColor Red
        exit 1
    }
    Set-Content -Path $DepsSentinel -Value $CurrentHash -Encoding ascii
    Write-Info "Dependencies installed."
} else {
    Write-Info "Dependencies already installed and up to date - skipping."
}

# ---------------------------------------------------------------------------
# Step 4: Playwright Chromium
# ---------------------------------------------------------------------------
Write-Step "Playwright Chromium browser"

$ChromiumSentinel = Join-Path $RepoRoot '.venv\.chromium-installed'

if ((-not $Reinstall) -and (Test-Path $ChromiumSentinel)) {
    Write-Info "Chromium already installed - skipping."
} else {
    # The corporate CA bundle was already exported above (NODE_EXTRA_CA_CERTS is
    # set), so the Playwright browser download works behind TLS inspection.
    Write-Info "Installing Chromium (this can take a few minutes on first run) ..."
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Playwright Chromium install failed." -ForegroundColor Red
        Write-Host "Manual fallback options:"
        Write-Host "  1. Set NODE_EXTRA_CA_CERTS to your exported corporate CA bundle and re-run."
        Write-Host "  2. Run '.venv\Scripts\python.exe -m playwright install chromium' on a machine"
        Write-Host "     with open internet egress, then copy its Playwright browser cache"
        Write-Host "     (%USERPROFILE%\AppData\Local\ms-playwright) to this machine."
        exit 1
    }
    Set-Content -Path $ChromiumSentinel -Value (Get-Date -Format 'o') -Encoding ascii
    Write-Info "Chromium installed."
}

# ---------------------------------------------------------------------------
# Step 5: .env
# ---------------------------------------------------------------------------
Write-Step "Environment configuration (.env)"

$EnvPath = Join-Path $RepoRoot '.env'
$EnvExamplePath = Join-Path $RepoRoot '.env.example'

if (-not (Test-Path $EnvPath)) {
    if (-not (Test-Path $EnvExamplePath)) {
        Write-Host ".env.example is missing - cannot bootstrap .env. Aborting." -ForegroundColor Red
        exit 1
    }
    Copy-Item -Path $EnvExamplePath -Destination $EnvPath
    Write-Host ""
    Write-Host ".env was missing, so it was created from .env.example." -ForegroundColor Yellow
    Write-Host "Fill in these values before running deploy.cmd again:"
    Write-Host "  - QMETRY_API_KEY"
    Write-Host "  - AZURE_AI_ENDPOINT / AZURE_AI_API_KEY"
    Write-Host "  - APP_BASE_URL"
    Write-Host "  - APP_USERNAME / APP_PASSWORD"
    Write-Host ""
    Write-Host "Edit .env, then double-click deploy.cmd again."
    exit 1
} else {
    Write-Info ".env already present - skipping."
}

if ($SetupOnly) {
    Write-Step "Setup complete (-SetupOnly) - not launching the server."
    exit 0
}

# ---------------------------------------------------------------------------
# Step 6: Launch
# ---------------------------------------------------------------------------
Write-Step "Starting Sous Chef QA console"

# Read SERVER_PORT (and HOST, for completeness) from .env if set.
$ServerPort = 8000
$ServerHost = '127.0.0.1'
Get-Content -Path $EnvPath | ForEach-Object {
    if ($_ -match '^\s*SERVER_PORT\s*=\s*(\S+)') { $ServerPort = $Matches[1] }
    if ($_ -match '^\s*SERVER_HOST\s*=\s*(\S+)') { $ServerHost = $Matches[1] }
}
$Url = "http://${ServerHost}:${ServerPort}"

Write-Info "Launching server.py ..."
$ServerProcess = Start-Process -FilePath $VenvPython -ArgumentList 'server.py' `
    -WorkingDirectory $RepoRoot -PassThru -WindowStyle Normal

Write-Info "Waiting for $Url/config to respond ..."
$deadline = (Get-Date).AddSeconds(30)
$up = $false
while ((Get-Date) -lt $deadline) {
    if ($ServerProcess.HasExited) {
        Write-Host "Server process exited unexpectedly (exit code $($ServerProcess.ExitCode))." -ForegroundColor Red
        exit 1
    }
    try {
        $resp = Invoke-WebRequest -Uri "$Url/config" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $up = $true; break }
    } catch {
        # not up yet
    }
    Start-Sleep -Milliseconds 500
}

if (-not $up) {
    Write-Warning "Server did not respond within 30s - it may still be starting. Check the server window."
} else {
    Write-Info "Server is up at $Url"
}

if (-not $NoBrowser) {
    Start-Process $Url
}

Write-Host ""
Write-Host "Sous Chef QA console is running at $Url" -ForegroundColor Green
Write-Host "Close the server window (or press Ctrl+C in it) to stop the console."
Write-Host ""

Wait-Process -Id $ServerProcess.Id
