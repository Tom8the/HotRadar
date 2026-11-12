param([ValidateRange(1024,65535)][int]$Port = 18811)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$nextCli = Join-Path $projectRoot 'frontend\node_modules\next\dist\bin\next'
Push-Location (Join-Path $projectRoot 'frontend')
try {
    node $nextCli dev --hostname 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Frontend startup failed.' }
} finally { Pop-Location }
