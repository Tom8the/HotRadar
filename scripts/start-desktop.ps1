param(
    [ValidateSet('Start', 'Stop')][string]$Action = 'Start',
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.runlogs\desktop'
$stateFile = Join-Path $runtimeRoot 'processes.json'
$webUrl = 'http://127.0.0.1:18811/'
$apiUrl = 'http://127.0.0.1:18810'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$rootHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())))
$launchMutex = [Threading.Mutex]::new($false, "Local\HotRadarDesktop-$rootHash")
$lockHeld = $false
$managed = @()
. (Join-Path $PSScriptRoot 'desktop-readiness.ps1')

function Write-LaunchLog([string]$Message) {
    Add-Content -LiteralPath (Join-Path $runtimeRoot 'launcher.log') -Encoding utf8 `
        -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
}

function Test-BackendReady {
    try {
        $health = Invoke-RestMethod "$apiUrl/api/health" -TimeoutSec 3
        return $health.service -eq 'HotRadar' -and $health.status -eq 'ok' `
            -and $health.database -eq 'connected'
    } catch { return $false }
}

function Test-FrontendReady {
    try {
        $page = Invoke-WebRequest $webUrl -TimeoutSec 5
        if ($page.StatusCode -ne 200 -or $page.Content -notmatch '<title>HotRadar') {
            return $false
        }
        $today = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
            [DateTimeOffset]::UtcNow, 'China Standard Time').ToString('yyyy-MM-dd')
        $contents = Invoke-RestMethod "${webUrl}api/contents?date=$today" -TimeoutSec 8
        return $contents.collection_mode -eq 'verified-platform-content'
    } catch { return $false }
}

function Get-Listener([int]$Port) {
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Get-ProjectWorker {
    # Includes an existing manually started project worker; never matches another venv.
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($projectRoot, [StringComparison]::OrdinalIgnoreCase) `
            -and $_.CommandLine -match '(hotradar\.worker|hotradar-worker)' `
            -and $_.Name -match '^(python|hotradar-worker)(\.exe)?$'
    })
}

function Save-ManagedProcesses {
    @{ project = $projectRoot; processes = @($script:managed) } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $stateFile -Encoding utf8
}

function Get-ManagedProcess($Entry) {
    $process = Get-Process -Id $Entry.id -ErrorAction SilentlyContinue
    if ($process -and $process.Path -eq $Entry.executable -and
        $process.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$Entry.started_at).UtcTicks) {
        return $process
    }
}

function Stop-ManagedComponent($Entry) {
    if (Get-ManagedProcess $Entry) {
        # Python venv launchers can have child processes; stop only this verified tree.
        & "$env:SystemRoot\System32\taskkill.exe" /PID $Entry.id /T /F *>> (Join-Path $runtimeRoot 'launcher.log')
        if ($LASTEXITCODE -ne 0) { throw "无法停止 $($Entry.role)。" }
        Write-LaunchLog "Stopped $($Entry.role)."
    }
    $script:managed = @($script:managed | Where-Object { $_.role -ne $Entry.role })
    Save-ManagedProcesses
}

function Read-JsonFile([string]$Path) {
    try { Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -AsHashtable }
    catch { return $null }
}

function Test-FrontendDependencies([string]$Directory, $Lock, [string]$Architecture) {
    $modules = @('next', '@next/env', "@next/swc-win32-$Architecture-msvc")
    foreach ($module in $modules) {
        $expected = $Lock.packages."node_modules/$module".version
        if (-not $expected) { continue }
        $installed = Read-JsonFile (Join-Path $Directory "node_modules/$module/package.json")
        if ($installed.version -ne $expected) { return $false }
    }
    return $true
}

function Start-Component([string]$Role, [string]$Executable, [string[]]$Arguments,
                         [string]$Directory) {
    $quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
    $process = Start-Process -FilePath $Executable -ArgumentList $quoted `
        -WorkingDirectory $Directory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeRoot "$Role.log") `
        -RedirectStandardError (Join-Path $runtimeRoot "$Role-error.log")
    $script:managed = @($script:managed | Where-Object { $_.role -ne $Role }) + @(@{
        role = $Role; id = $process.Id; executable = $Executable
        started_at = $process.StartTime.ToUniversalTime().ToString('o')
    })
    Save-ManagedProcesses
    Write-LaunchLog "Started $Role (PID $($process.Id))."
    return $process
}

function Wait-Component([string]$Role, [scriptblock]$Ready, $Process) {
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (& $Ready) { return }
        if ($Process -and $Process.HasExited) {
            throw "$Role 已退出。请查看 $runtimeRoot\$Role-error.log。"
        }
        Start-Sleep -Seconds 1
    }
    throw "$Role 启动超时。请查看 $runtimeRoot 中的日志。"
}

function Invoke-SetupCommand([string]$Executable, [string[]]$Arguments, [string]$Step) {
    & $Executable @Arguments *>> (Join-Path $runtimeRoot 'setup.log')
    if ($LASTEXITCODE -ne 0) { throw "$Step 失败。请查看 $runtimeRoot\setup.log。" }
}

try {
    try { $lockHeld = $launchMutex.WaitOne($(if ($Action -eq 'Stop') { 180000 } else { 0 })) }
    catch [Threading.AbandonedMutexException] { $lockHeld = $true }
    if (-not $lockHeld) {
        if ($Action -eq 'Stop') { throw '另一个 HotRadar 启停操作仍在运行，请稍后重试。' }
        Write-LaunchLog 'Another launch is still running; duplicate click ignored.'
        Write-Output 'HotRadar正在启动，请等待现有启动完成。'
        return
    }
    if (Test-Path -LiteralPath $stateFile) {
        $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
        if ($state.project -ne $projectRoot) { throw '启动状态目录与当前项目不一致。' }
        $managed = @($state.processes)
    }
    if ($Action -eq 'Stop') {
        foreach ($entry in @($managed | Sort-Object role -Descending)) {
            Stop-ManagedComponent $entry
        }
        $managed = @()
        Save-ManagedProcesses
        Write-Output '已停止此启动器创建的进程；数据库及历史保留。'
        return
    }

    Push-Location $projectRoot
    try {
        $uv = (Get-Command uv.exe -ErrorAction Stop).Source
        $node = (Get-Command node.exe -ErrorAction Stop).Source
        $docker = (Get-Command docker.exe -ErrorAction Stop).Source
        $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
        $frontendRoot = Join-Path $projectRoot 'frontend'
        $next = Join-Path $frontendRoot 'node_modules\next\dist\bin\next'
        $frontendLock = Read-JsonFile (Join-Path $frontendRoot 'package-lock.json')
        $nextVersion = $frontendLock.packages.'node_modules/next'.version
        if (-not $nextVersion) { throw '前端锁文件中缺少 Next.js 版本。' }
        $architecture = (& $node -p 'process.arch').Trim()
        $dependenciesReady = Test-FrontendDependencies $frontendRoot $frontendLock $architecture
        $buildId = Join-Path $frontendRoot '.next\BUILD_ID'
        $buildVersion = (Read-JsonFile (Join-Path $frontendRoot '.next\diagnostics\framework.json')).version
        $frontendInputs = @(Get-ChildItem frontend/src, frontend/public -File -Recurse -ErrorAction SilentlyContinue) +
            @(Get-Item frontend/package.json, frontend/package-lock.json, frontend/next.config.*,
                frontend/tsconfig.json, frontend/.env.local -ErrorAction SilentlyContinue)
        $backendInputs = @(Get-ChildItem backend/src, migrations -File -Filter '*.py' -Recurse) +
            @(Get-Item pyproject.toml, uv.lock, .env -ErrorAction SilentlyContinue)
        $needsBuild = -not (Test-Path -LiteralPath $buildId) -or $buildVersion -ne $nextVersion
        if (-not $needsBuild) {
            $buildTime = (Get-Item -LiteralPath $buildId).LastWriteTimeUtc
            $needsBuild = @($frontendInputs | Where-Object { $_.LastWriteTimeUtc -gt $buildTime }).Count -gt 0
        }
        foreach ($entry in @($managed)) {
            $process = Get-ManagedProcess $entry
            if (-not $process) { continue }
            $started = $process.StartTime.ToUniversalTime()
            $inputs = if ($entry.role -eq 'frontend') { $frontendInputs } else { $backendInputs }
            $changed = @($inputs | Where-Object { $_.LastWriteTimeUtc -gt $started }).Count -gt 0
            if ($changed -or ($entry.role -eq 'frontend' -and ($needsBuild -or -not $dependenciesReady))) {
                Write-LaunchLog "Restarting $($entry.role) after project files or dependencies changed."
                Stop-ManagedComponent $entry
            }
        }
        $apiReady = Test-BackendReady

        if (-not $apiReady) {
            if ((Get-Listener 18810).Count) { throw '端口18810已被占用，且不是可用的HotRadar后端。' }
            & $docker info --format '{{.ServerVersion}}' *> $null
            if ($LASTEXITCODE -ne 0) {
                $dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
                if (-not (Test-Path -LiteralPath $dockerDesktop)) { throw '请先安装并启动Docker Desktop。' }
                Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
                $deadline = [DateTime]::UtcNow.AddSeconds(150)
                do {
                    Start-Sleep -Seconds 2
                    & $docker info --format '{{.ServerVersion}}' *> $null
                    if ($LASTEXITCODE -eq 0) { break }
                } while ([DateTime]::UtcNow -lt $deadline)
                if ($LASTEXITCODE -ne 0) { throw 'Docker尚未就绪，请在Docker Desktop中检查后重试。' }
            }
            if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
                & (Join-Path $PSScriptRoot 'configure-local.ps1') *>> `
                    (Join-Path $runtimeRoot 'setup.log')
            }
            $browserPackage = Join-Path $projectRoot '.venv\Lib\site-packages\playwright\__init__.py'
            if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $browserPackage)) {
                Invoke-SetupCommand $uv @('sync', '--locked', '--extra', 'dev', '--extra', 'browser-auth',
                    '--python', '3.12') '安装后端依赖'
            }
            Invoke-SetupCommand $docker @('compose', 'up', '-d', 'postgres') '启动数据库容器'
            Wait-DesktopDatabase -Probe {
                $containerId = & $docker compose ps -a -q postgres 2>> (Join-Path $runtimeRoot 'setup.log')
                if ($LASTEXITCODE -ne 0) { return 'unavailable' }
                if (-not $containerId) { return 'missing' }
                $containerState = & $docker inspect --format '{{.State.Status}}' $containerId `
                    2>> (Join-Path $runtimeRoot 'setup.log')
                if ($LASTEXITCODE -ne 0) { return 'unavailable' }
                if ($containerState -ne 'running') { return $containerState }
                $sql = & $docker compose exec -T postgres psql -U hotradar -d hotradar `
                    -Atqc 'SELECT 1' 2>> (Join-Path $runtimeRoot 'setup.log')
                if ($LASTEXITCODE -eq 0 -and "$sql".Trim() -eq '1') { return 'ready' }
                return 'recovering'
            } -Log ${function:Write-LaunchLog}
            Invoke-SetupCommand $uv @('run', '--locked', 'alembic', 'upgrade', 'head') '数据库升级'
            Invoke-SetupCommand $uv @('run', '--locked', 'hotradar-seed') '注册既定来源'
            $backend = Start-Component 'backend' $python @('-m', 'uvicorn',
                'hotradar.api.main:app', '--host', '127.0.0.1', '--port', '18810') $projectRoot
            Wait-Component 'backend' { Test-BackendReady } $backend
        } else { Write-LaunchLog 'Reused healthy backend.' }

        # A previously running frontend can become ready once its backend is recovered.
        $webReady = Test-FrontendReady
        if (-not $webReady) {
            if ((Get-Listener 18811).Count) { throw '端口18811已被占用，且网页或后端代理尚未就绪。' }
            Push-Location $frontendRoot
            try {
                if (-not $dependenciesReady -or -not (Test-Path -LiteralPath $next)) {
                    Invoke-SetupCommand (Get-Command npm.cmd).Source @('ci') '安装前端依赖'
                    if (-not (Test-FrontendDependencies $frontendRoot $frontendLock $architecture)) {
                        throw '前端安装版本仍与锁文件不一致。'
                    }
                    $needsBuild = $true
                }
                if ($needsBuild) { Invoke-SetupCommand (Get-Command npm.cmd).Source @('run', 'build') '构建网页' }
                $frontend = Start-Component 'frontend' $node @($next, 'start', '--hostname',
                    '127.0.0.1', '--port', '18811') (Get-Location).Path
                Wait-Component 'frontend' { Test-FrontendReady } $frontend
            } finally { Pop-Location }
        } else { Write-LaunchLog 'Reused healthy frontend and API proxy.' }

        $workers = Get-ProjectWorker
        if (-not $workers.Count) {
            $worker = Start-Component 'worker' $python @('-m', 'hotradar.worker') $projectRoot
            Start-Sleep -Seconds 2
            if ($worker.HasExited) { throw '采集进程已退出，请查看worker-error.log。' }
        } else { Write-LaunchLog 'Reused existing project worker.' }
        if (-not (Test-FrontendReady)) { throw '网页最终检查未通过。' }
        if (-not $NoBrowser) { Start-Process $webUrl }
        Write-LaunchLog "Ready: $webUrl; browser requested: $(-not $NoBrowser)."
        Write-Output "HotRadar已就绪：$webUrl"
    } finally { Pop-Location }
} catch {
    Write-LaunchLog "Startup failed: $($_.Exception.Message)"
    if (-not $NoBrowser) {
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show(
            "$($_.Exception.Message)`n`n启动日志：$runtimeRoot", 'HotRadar 启动未完成') | Out-Null
    }
    Write-Error $_.Exception.Message
    exit 1
} finally {
    if ($lockHeld) { $launchMutex.ReleaseMutex() }
    $launchMutex.Dispose()
}
