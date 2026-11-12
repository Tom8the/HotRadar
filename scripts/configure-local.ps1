$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    $secretBytes = [byte[]]::new(36)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($secretBytes)
    $databasePassword = [Convert]::ToBase64String($secretBytes)
    $settings = "HOTRADAR_DB_HOST=127.0.0.1`nHOTRADAR_DB_PORT=18814`nHOTRADAR_DB_NAME=hotradar`nHOTRADAR_DB_USER=hotradar`nHOTRADAR_DB_PASSWORD=$databasePassword`nHOTRADAR_COLLECTION_INTERVAL=900`nHOTRADAR_REQUEST_TIMEOUT=20`nHOTRADAR_TIMEZONE=Asia/Shanghai`n"
    [IO.File]::WriteAllText($envFile, $settings, [Text.UTF8Encoding]::new($false))
    Write-Output 'Created project-local .env with a generated database password.'
} else {
    Write-Output 'Existing project-local .env preserved.'
}
