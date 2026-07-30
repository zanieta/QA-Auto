@echo off
rem Double-click entry point for the Sous Chef QA console.
rem Delegates to scripts\deploy.ps1 (idempotent setup + launch).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" %*
