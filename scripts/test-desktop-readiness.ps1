$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'desktop-readiness.ps1')

# Recovery must continue even after the container health has failed. No Docker
# mutation, real database restart, or production fixture data is needed here.
$script:probes = [Collections.Generic.Queue[string]]::new()
@('recovering', 'recovering', 'ready') | ForEach-Object { $script:probes.Enqueue($_) }
$script:notices = [Collections.Generic.List[string]]::new()
Wait-DesktopDatabase -Probe { $script:probes.Dequeue() } `
    -Log { param($message) $script:notices.Add($message) } -PollSeconds 0
if ($script:probes.Count -ne 0 -or $script:notices[-1] -notmatch 'SQL ready') {
    throw 'Recovery did not reach real SQL readiness.'
}

foreach ($state in @('exited', 'dead', 'missing')) {
    $failed = $false
    try {
        Wait-DesktopDatabase -Probe { $state } -Log {} -PollSeconds 0
    } catch { $failed = $_.Exception.Message -match '数据库容器已停止或不存在' }
    if (-not $failed) { throw "Stopped container was not rejected: $state" }
}

$timedOut = $false
try {
    Wait-DesktopDatabase -Probe { 'recovering' } -Log {} -TimeoutSeconds 0 -PollSeconds 0
} catch { $timedOut = $_.Exception.Message -match '0秒内仍未就绪' }
if (-not $timedOut) { throw 'Unready database did not time out.' }

# A ready SQL response at the deadline is accepted before checking the timeout.
Wait-DesktopDatabase -Probe { 'ready' } -Log {} -TimeoutSeconds 0 -PollSeconds 0
Write-Output 'PASS: recovery, stopped/missing container, timeout, and SQL-ready boundary.'
