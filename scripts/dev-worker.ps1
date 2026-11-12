$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    uv run --locked --extra dev hotradar-worker
    if ($LASTEXITCODE -ne 0) { throw 'HotRadar worker exited with an error.' }
} finally { Pop-Location }
