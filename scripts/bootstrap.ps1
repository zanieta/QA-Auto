# Bootstrap a fresh clone into a runnable state on a Duke Windows machine.
#
# WHY THIS EXISTS: `.venv/` is gitignored, and the pip config that bypasses
# Duke's corporate TLS inspection lives at `.venv/pip.ini` — so it does NOT
# travel with the repo. On a fresh clone `pip install` fails with SSL
# certificate errors, which looks like a broken network rather than a missing
# config. `frontend/.npmrc` (strict-ssl=false) IS tracked, so npm is fine; this
# script closes the pip half of the same gap and does the rest of the setup.
#
# Safe to re-run: every step is idempotent.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
Write-Host "Repo: $repo" -ForegroundColor Cyan

# --- 1. Python ---------------------------------------------------------------
$py = $null
foreach ($c in @('py -3.14', 'py -3', 'python')) {
    $exe, $arg = $c.Split(' ', 2)
    if (Get-Command $exe -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) { throw "No Python found. Install Python 3.11+ (3.14 is what this project uses)." }
Write-Host "Using Python launcher: $py"

# --- 2. Virtualenv -----------------------------------------------------------
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host "Creating .venv ..." -ForegroundColor Yellow
    Invoke-Expression "$py -m venv .venv"
} else {
    Write-Host ".venv already present - reusing"
}
$venvPy = Join-Path $repo '.venv\Scripts\python.exe'

# --- 3. The pip config that does not travel ---------------------------------
# Duke's TLS inspection re-signs PyPI traffic, so pip must be told to trust
# those hosts. This is the same content the working machine has; without it
# every `pip install` dies on a certificate error.
$pipIni = Join-Path $repo '.venv\pip.ini'
$pipIniBody = @'
[global]
trusted-host = pypi.org
               files.pythonhosted.org
               pypi.python.org
'@
if (-not (Test-Path $pipIni)) {
    Write-Host "Writing .venv\pip.ini (corporate TLS bypass) ..." -ForegroundColor Yellow
    Set-Content -Path $pipIni -Value $pipIniBody -Encoding ascii
} else {
    Write-Host ".venv\pip.ini already present - leaving it alone"
}

# --- 4. Python dependencies --------------------------------------------------
Write-Host "Installing Python dependencies ..." -ForegroundColor Yellow
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt

# --- 5. Chromium for Playwright ---------------------------------------------
Write-Host "Installing Chromium for Playwright ..." -ForegroundColor Yellow
& $venvPy -m playwright install chromium

# --- 6. Frontend -------------------------------------------------------------
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host "Installing frontend dependencies ..." -ForegroundColor Yellow
    Push-Location (Join-Path $repo 'frontend')
    npm install
    npm run build
    Pop-Location
} else {
    Write-Host "npm not found - skipping the frontend. Install Node, then:" -ForegroundColor Red
    Write-Host "    cd frontend; npm install; npm run build"
}

# --- 7. .env -----------------------------------------------------------------
# Never generated automatically: it holds real secrets, and a half-filled .env
# that loads is worse than an obviously missing one.
if (-not (Test-Path '.env')) {
    Write-Host ""
    Write-Host "NO .env FOUND - the app will not run without it." -ForegroundColor Red
    Write-Host "  copy .env.example .env    then fill in:" -ForegroundColor Yellow
    Write-Host "    AZURE_AI_ENDPOINT, AZURE_AI_API_KEY"
    Write-Host "    QMETRY_API_KEY, QMETRY_PROJECT_ID"
    Write-Host "    JIRA_API_TOKEN"
    Write-Host "    APP_BASE_URL, APP_USERNAME, APP_PASSWORD"
} else {
    # Flag any documented key that is missing, since a missing key does not
    # error - it silently falls back (see QMETRY_PROJECT_ID in .env.example).
    $have = (Get-Content '.env' | Where-Object { $_ -match '^\s*[A-Z_]+=' } |
             ForEach-Object { ($_ -split '=', 2)[0].Trim() })
    $want = (Get-Content '.env.example' | Where-Object { $_ -match '^\s*[A-Z_]+=' } |
             ForEach-Object { ($_ -split '=', 2)[0].Trim() })
    $missing = $want | Where-Object { $have -notcontains $_ }
    if ($missing) {
        Write-Host ""
        Write-Host "Keys in .env.example missing from your .env:" -ForegroundColor Yellow
        $missing | ForEach-Object { Write-Host "    $_" }
        Write-Host "  (some are optional and have defaults - check .env.example comments)"
    } else {
        Write-Host ".env present with every documented key" -ForegroundColor Green
    }
}

# --- 8. Prove it ------------------------------------------------------------
Write-Host ""
Write-Host "Running the test suite ..." -ForegroundColor Cyan
& $venvPy -m pytest tests/ -q

Write-Host ""
Write-Host "Done. Start the app with:" -ForegroundColor Green
Write-Host "    .venv\Scripts\python.exe server.py     (backend on :8000)"
Write-Host "    cd frontend; npm run dev               (console on :5173)"
