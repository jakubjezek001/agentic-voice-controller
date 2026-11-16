# One-time setup on Windows. Usage: scripts\setup.cmd [--no-models]
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python and package manager, user-local)..."
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

function Invoke-Step([string[]]$StepArgs) {
    & uv @StepArgs
    if ($LASTEXITCODE -ne 0) { throw "uv $($StepArgs -join ' ') failed" }
}

Invoke-Step @("sync")
Invoke-Step @("run", "vc", "init")
if ($args -notcontains "--no-models") {
    Invoke-Step @("run", "vc", "models", "download")
}
& uv run vc doctor
Write-Host ""
Write-Host "Done. Try: .\vc.cmd record start --name test   then   .\vc.cmd record stop --analyze"
