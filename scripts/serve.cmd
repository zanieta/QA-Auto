@echo off
rem Watchdog for the QA console server ("SousChef QA Console" scheduled task).
rem If nothing is listening on port 8000, start server.py and log to logs\server.log.

netstat -ano | findstr "LISTENING" | findstr ":8000 " >nul
if %errorlevel%==0 exit /b 0

cd /d "%~dp0.."
if not exist logs mkdir logs
echo [watchdog %date% %time%] server not running - starting >> logs\server.log
start "" /b cmd /c ".venv\Scripts\python.exe server.py >> logs\server.log 2>&1"
