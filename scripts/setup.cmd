@echo off
rem One-time setup on Windows. Double-click or run: scripts\setup.cmd [--no-models]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
