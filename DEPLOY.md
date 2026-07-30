**INTERNAL ONLY — this package may contain Duke credentials. Do not forward
outside Duke or upload to personal cloud storage (OneDrive personal, Dropbox,
personal email, etc.).**

# Sous Chef QA Console — quick start

## 1. Prerequisite: Python

Install Python 3.11 or newer from **https://www.python.org/downloads/**

During install, check the box **"Add python.exe to PATH"**. If you already
have Python 3.11+ on PATH, skip this step.

## 2. Run it

1. Unzip this package anywhere (e.g. `Desktop\SousChef-QA-Console`).
2. Double-click **`deploy.cmd`**.
3. Wait. The **first run** sets up a private Python environment and downloads
   a browser for testing (Chromium) — this can take a few minutes. **Later
   runs** just launch, in a few seconds.
4. Your browser opens automatically to **http://127.0.0.1:8000**.

That's it — the console is running.

## Stopping the console

Close the black server window that opened (or press `Ctrl+C` inside it). This
stops the local server; your browser tab will stop working.

## Troubleshooting

**"Python not found" / deploy.cmd exits immediately with a red message**
Python isn't installed or isn't on PATH. Reinstall from python.org and make
sure "Add python.exe to PATH" is checked, then double-click `deploy.cmd`
again.

**A message about filling in `.env`**
This means credentials weren't shipped with your copy of the package. Open the
newly created `.env` file in a text editor, fill in the listed values, save,
and double-click `deploy.cmd` again.

**Playwright / browser download fails with an SSL or certificate error**
This usually happens on a Duke laptop with corporate SSL inspection turned on.
`deploy.ps1` already tries to work around this automatically by exporting your
machine's trusted certificates. If it still fails:
- Re-run `deploy.cmd` once more (sometimes a transient network blip).
- Or ask IT/a teammate to run `deploy.cmd -SetupOnly` on a machine with open
  internet access, then copy the folder
  `%USERPROFILE%\AppData\Local\ms-playwright` from that machine onto yours.

**Port 8000 is already in use**
Open `.env` in a text editor and change `SERVER_PORT=8000` to another number
(e.g. `8010`), save, and double-click `deploy.cmd` again.

**Nothing opens in the browser but the black window is still running**
Wait a few more seconds — Azure/QMetry connectivity checks can be slow on a
busy network — then open http://127.0.0.1:8000 (or your custom port)
manually.
