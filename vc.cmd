@echo off
rem Voice Coach command wrapper for Windows (cmd / PowerShell). Usage: vc.cmd <command>
uv run --quiet --project "%~dp0." voicecoach %*
