# Portable Windows local-deploy package

**Date:** 2026-07-30
**Status:** DESIGN — not yet implemented.

**Directed by Roman (chat):** "package this into something that can be easily
deployed locally" → for **other testers' Windows PCs**, as a **one-command
setup+run script**, shipping the **same `.env`** inside the port (zero-config
for testers).

## Goal

A Duke tester unzips one folder, double-clicks one file, and the Sous Chef QA
console is running at `http://127.0.0.1:8000` — no manual venv / Node /
Playwright plumbing, no config to fill in.

## Decisions (settled with Roman)

1. **Target:** other testers' Windows machines (portable).
2. **Form:** one-command setup+run script (not Docker, not a frozen exe).
3. **Ship the prebuilt UI** (`frontend/dist`) so Node is never needed on a
   tester's machine. `server.py` already mounts `frontend/dist` at `/` and
   serves it on `127.0.0.1:8000` (verified: `server.py:660-662`, `674-678`).
4. **Ship the same `.env`** (shared credentials) inside the package so testers
   run with zero config.

## Security posture (explicit)

The package, when built with secrets, contains LIVE credentials (QMetry API key,
Azure key/endpoint, `APP_USERNAME`/`APP_PASSWORD`). Guardrails:

- `package.ps1` embeds `.env` ONLY when invoked with an explicit `-IncludeEnv`
  switch; without it, the package ships `.env.example` and the deploy script
  halts for the tester to supply `.env`. This prevents accidentally shipping
  secrets.
- `DEPLOY.md` opens with a bold "INTERNAL ONLY — this package contains
  credentials; do not forward outside Duke / do not upload to personal cloud"
  banner.
- Note recorded for Roman: distributing the QMetry key to more machines widens
  its exposure — rotating it first is advisable (pre-existing open item).

## Components

### 1. `deploy.cmd` (repo root) — the double-clickable entry point
A thin wrapper that invokes PowerShell on `scripts/deploy.ps1` with an
execution policy bypass for the current process only:
```
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" %*
```
(A `.ps1` is not double-click-runnable by default; the `.cmd` is.)

### 2. `scripts/deploy.ps1` — idempotent setup + launch
Runs from the repo root (derives it from `$PSScriptRoot\..`). Steps, each
skipping when already satisfied:

1. **Python check** — require `python` (or `py -3`) on PATH, version ≥ 3.11.
   If missing/too old: print the python.org download URL and the exact version
   needed, then exit non-zero. (We cannot reliably auto-install Python without
   admin — this is the one documented prerequisite.)
2. **venv** — if `.venv` is absent, create it (`python -m venv .venv`).
3. **pip deps** — upgrade pip, then
   `.venv\Scripts\python.exe -m pip install --upgrade --use-feature=truststore -r requirements.txt`.
   `--use-feature=truststore` makes pip use the **Windows cert store**, so
   corporate TLS inspection doesn't break installs — no hardcoded CA, no
   `--trusted-host` (honors the repo rule). A sentinel file
   (`.venv\.deps-installed`) records the requirements hash so re-runs skip a
   no-op reinstall.
4. **Playwright Chromium** — if not already installed, run
   `.venv\Scripts\python.exe -m playwright install chromium`. Corporate-SSL
   handling: before the download, export the machine's trusted root CAs to a
   PEM bundle (`scripts\_corp-ca-bundle.pem`) via PowerShell
   (`Get-ChildItem Cert:\LocalMachine\Root` → base64 PEM) and set
   `NODE_EXTRA_CA_CERTS` to it for the download process. Best-effort: if the
   download still fails, print the manual fallback (set `NODE_EXTRA_CA_CERTS`
   to an exported corporate CA, or run `playwright install chromium` on a
   machine with open egress and copy the browser cache).
5. **.env** — if `.env` is missing: copy `.env.example` → `.env` and exit with
   a message listing the values to fill. (When the package was built with
   `-IncludeEnv`, `.env` is already present and this step is a no-op.)
6. **Launch** — start `.venv\Scripts\python.exe server.py`; wait until
   `http://127.0.0.1:8000/config` responds; open the default browser to
   `http://127.0.0.1:8000`. Leave the server running in the foreground (closing
   the window stops it).

Flags: `-Reinstall` (force venv + deps + chromium rebuild), `-NoBrowser`
(skip opening the browser), `-SetupOnly` (do steps 1–5, don't launch).

### 3. `scripts/package.ps1` — build the shareable zip (run by the maintainer)
1. Build the frontend: `cd frontend; npm run build` (produces `frontend/dist`).
   Requires Node on the MAINTAINER's machine only.
2. Stage the distributable file set (see below) into a temp dir.
3. `-IncludeEnv`: also stage the real `.env`; otherwise stage `.env.example`
   only and print that the package is secrets-free.
4. `Compress-Archive` the staged dir → `SousChef-QA-Console.zip` at the repo
   root (overwrite with confirmation).
5. Print a summary: included/excluded, whether secrets were embedded, and the
   zip size.

**Included:** `agent/`, `prompts/`, `frontend/dist/`, `frontend/public/`
(logo asset), `scripts/deploy.ps1`, `scripts/serve.cmd`, `deploy.cmd`,
`server.py`, `main.py`, `requirements.txt`, `.env.example`, `DEPLOY.md`,
`CLAUDE.md`, `FRONTEND.md`, `reports/.gitkeep`. Plus `.env` when `-IncludeEnv`.

**Excluded (always):** `.venv/`, `frontend/node_modules/`, `frontend/src/`
(not needed with prebuilt dist), `.git/`, `__pycache__/`, `*.pyc`,
`reports/run_*.html`, `manual_sessions/*.json`, `logs/*`, `docs/`, `tests/`,
`.pytest_cache/`, `scripts/_corp-ca-bundle.pem`, any existing
`SousChef-QA-Console.zip`.

### 4. `DEPLOY.md` — tester-facing quickstart
- Bold INTERNAL-ONLY / contains-credentials banner (top).
- Prereq: install Python 3.11+ from python.org (check "Add to PATH").
- Steps: unzip → double-click `deploy.cmd` → wait for the browser to open at
  `:8000`. First run installs everything (few minutes + a browser download);
  later runs just launch.
- Troubleshooting: corporate SSL / Playwright download failure (the
  `NODE_EXTRA_CA_CERTS` fallback), port 8000 already in use
  (`SERVER_PORT` in `.env`), "Python not found" (PATH), how to stop (close the
  window).

## Testing / acceptance

Automated tests are not the right tool for a Windows bootstrap script; this is
verified by execution:
- `-SetupOnly` on a machine that already has the venv → detects state, does no
  redundant work, exits 0.
- Fresh clone (rename `.venv` aside) → `deploy.ps1` recreates venv, installs
  deps via truststore, installs chromium, and launches to a responding `:8000`.
- `package.ps1` (no flag) → zip contains `.env.example`, NOT `.env`; `-IncludeEnv`
  → zip contains `.env`; excluded dirs absent in both.
- Unzip the produced package into a clean dir and run `deploy.cmd` end-to-end →
  console reachable at `:8000`.
The plan will script these checks where possible and call out the manual ones.

## Out of scope

- Auto-installing Python (needs admin; documented prereq instead).
- Docker / cross-platform (explicitly rejected).
- A frozen `.exe` (explicitly rejected).
- Shipping the Chromium browser binaries inside the zip (too large/fragile;
  `playwright install` fetches them on first setup).
