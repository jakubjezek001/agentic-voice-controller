@echo off
rem Agentic Voice Controller command wrapper for Windows (cmd / PowerShell). Usage: vc.cmd <command>
uv run --quiet --project "%~dp0." vc %*
