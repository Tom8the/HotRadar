$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & (Join-Path $PSScriptRoot 'configure-local.ps1')
    uv sync --locked --extra dev --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Backend installation failed.' }
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Database configuration failed.' }
    docker compose up -d --wait postgres
    if ($LASTEXITCODE -ne 0) { throw 'Database startup failed.' }
    uv run --locked --extra dev alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
    uv run --locked --extra dev hotradar-seed
    if ($LASTEXITCODE -ne 0) { throw 'Source registration failed.' }
    uv run --locked --extra dev hotradar-worker --once
    if ($LASTEXITCODE -ne 0) { throw 'Initial source collection did not succeed.' }
    Write-Output 'HotRadar data environment is ready. Start backend, frontend and worker.'
} finally { Pop-Location }
