param([ValidateRange(1024,65535)][int]$Port = 18810)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    uv run --locked --extra dev uvicorn hotradar.api.main:app --host 127.0.0.1 --port $Port --reload
    if ($LASTEXITCODE -ne 0) { throw 'Backend startup failed.' }
} finally { Pop-Location }
