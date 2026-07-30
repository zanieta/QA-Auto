<#
.SYNOPSIS
    Builds the shareable "SousChef-QA-Console.zip" local-deploy package.
    Run by the maintainer (needs Node installed to build the frontend).

.DESCRIPTION
    1. Builds the frontend (frontend/dist) via `npm run build`.
    2. Stages the distributable file set into a temp directory.
    3. Without -IncludeEnv: stages .env.example only (secrets-free package).
       With -IncludeEnv: also stages the real .env and prints a loud warning.
    4. Compresses the staged directory into SousChef-QA-Console.zip at the
       repo root (-Force, overwrites any existing zip).
    5. Prints a summary and cleans up the temp staging directory.

.PARAMETER IncludeEnv
    Embed the real .env (with live credentials) in the package. Without this
    switch the package ships .env.example only and testers must fill in .env
    themselves (deploy.ps1 halts and prompts them to do so).
#>
[CmdletBinding()]
param(
    [switch]$IncludeEnv
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# Step 1: build the frontend
# ---------------------------------------------------------------------------
Write-Step "Building frontend (npm run build)"

$FrontendDir = Join-Path $RepoRoot 'frontend'
Push-Location $FrontendDir
try {
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm run build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$DistDir = Join-Path $FrontendDir 'dist'
if (-not (Test-Path $DistDir)) {
    throw "frontend/dist was not produced by the build - aborting."
}
Write-Host "   frontend/dist built OK."

# ---------------------------------------------------------------------------
# Step 2: stage the distributable file set
# ---------------------------------------------------------------------------
Write-Step "Staging package contents"

$StageDir = Join-Path $env:TEMP ("souschef-qa-package-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $StageDir | Out-Null

function Copy-Tree {
    <# Recursively copy a source directory into the stage, excluding
       __pycache__ dirs and *.pyc files (and, for reports/, run_*.html). #>
    param(
        [string]$Source,
        [string]$RelDest
    )
    $Dest = Join-Path $StageDir $RelDest
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    Get-ChildItem -Path $Source -Recurse -Force | ForEach-Object {
        $rel = $_.FullName.Substring($Source.Length).TrimStart('\','/')
        if ($rel -match '(^|\\)__pycache__(\\|$)') { return }
        if ($rel -like '*.pyc') { return }
        if ($rel -like 'run_*.html') { return }
        $target = Join-Path $Dest $rel
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Path $target -Force | Out-Null
        } else {
            $targetParent = Split-Path -Parent $target
            if (-not (Test-Path $targetParent)) {
                New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
            }
            Copy-Item -Path $_.FullName -Destination $target -Force
        }
    }
}

function Copy-File {
    param(
        [string]$Source,
        [string]$RelDest
    )
    $Dest = Join-Path $StageDir $RelDest
    $DestParent = Split-Path -Parent $Dest
    if (-not (Test-Path $DestParent)) {
        New-Item -ItemType Directory -Path $DestParent -Force | Out-Null
    }
    if (-not (Test-Path $Source)) {
        throw "Expected file not found: $Source"
    }
    Copy-Item -Path $Source -Destination $Dest -Force
}

# Directories (recursive, filtered)
Copy-Tree -Source (Join-Path $RepoRoot 'agent')          -RelDest 'agent'
Copy-Tree -Source (Join-Path $RepoRoot 'prompts')        -RelDest 'prompts'
Copy-Tree -Source (Join-Path $RepoRoot 'frontend\dist')  -RelDest 'frontend\dist'
Copy-Tree -Source (Join-Path $RepoRoot 'frontend\public') -RelDest 'frontend\public'

# Individual files
Copy-File -Source (Join-Path $RepoRoot 'scripts\deploy.ps1') -RelDest 'scripts\deploy.ps1'
Copy-File -Source (Join-Path $RepoRoot 'scripts\serve.cmd')  -RelDest 'scripts\serve.cmd'
Copy-File -Source (Join-Path $RepoRoot 'deploy.cmd')         -RelDest 'deploy.cmd'
Copy-File -Source (Join-Path $RepoRoot 'server.py')          -RelDest 'server.py'
Copy-File -Source (Join-Path $RepoRoot 'main.py')            -RelDest 'main.py'
Copy-File -Source (Join-Path $RepoRoot 'requirements.txt')   -RelDest 'requirements.txt'
Copy-File -Source (Join-Path $RepoRoot '.env.example')       -RelDest '.env.example'
Copy-File -Source (Join-Path $RepoRoot 'DEPLOY.md')          -RelDest 'DEPLOY.md'
Copy-File -Source (Join-Path $RepoRoot 'CLAUDE.md')          -RelDest 'CLAUDE.md'
Copy-File -Source (Join-Path $RepoRoot 'FRONTEND.md')        -RelDest 'FRONTEND.md'
Copy-File -Source (Join-Path $RepoRoot 'reports\.gitkeep')   -RelDest 'reports\.gitkeep'

$SecretsEmbedded = $false
if ($IncludeEnv) {
    $EnvPath = Join-Path $RepoRoot '.env'
    if (-not (Test-Path $EnvPath)) {
        throw "-IncludeEnv was passed but .env does not exist at $EnvPath"
    }
    Copy-File -Source $EnvPath -RelDest '.env'
    $SecretsEmbedded = $true
    Write-Host ""
    Write-Host "*** WARNING: -IncludeEnv was passed. The zip now contains LIVE CREDENTIALS" -ForegroundColor Red
    Write-Host "*** (QMetry API key, Azure key, app username/password). Handle accordingly." -ForegroundColor Red
    Write-Host ""
} else {
    Write-Host "   No -IncludeEnv flag - package is secrets-free (.env.example only)."
}

# ---------------------------------------------------------------------------
# Step 3: compress
# ---------------------------------------------------------------------------
Write-Step "Compressing package"

$ZipPath = Join-Path $RepoRoot 'SousChef-QA-Console.zip'
if (Test-Path $ZipPath) {
    Remove-Item -Path $ZipPath -Force
}

Compress-Archive -Path (Join-Path $StageDir '*') -DestinationPath $ZipPath -Force

$ZipSizeMB = [Math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
$TopLevelItems = Get-ChildItem -Path $StageDir | Select-Object -ExpandProperty Name | Sort-Object

# ---------------------------------------------------------------------------
# Step 4: cleanup + summary
# ---------------------------------------------------------------------------
Remove-Item -Path $StageDir -Recurse -Force

Write-Step "Package summary"
Write-Host "   Zip:              $ZipPath"
Write-Host "   Size:             $ZipSizeMB MB"
Write-Host "   Secrets embedded: $(if ($SecretsEmbedded) { 'YES - .env included' } else { 'no - .env.example only' })"
Write-Host "   Top-level items:"
$TopLevelItems | ForEach-Object { Write-Host "     - $_" }
Write-Host ""
