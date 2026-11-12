# Keep recovery waiting separate from container health: PostgreSQL can be
# temporarily unhealthy while safely recovering the existing data volume.
function Wait-DesktopDatabase {
    param(
        [Parameter(Mandatory)][scriptblock]$Probe,
        [Parameter(Mandatory)][scriptblock]$Log,
        [ValidateRange(0, 3600)][int]$TimeoutSeconds = 600,
        [ValidateRange(0, 30)][int]$PollSeconds = 2
    )
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $nextNotice = 0
    while ($true) {
        $status = & $Probe
        if ($status -eq 'ready') {
            & $Log "Database SQL ready after $([int]$watch.Elapsed.TotalSeconds)s."
            return
        }
        if ($status -in @('exited', 'dead', 'removing', 'missing')) {
            throw '数据库容器已停止或不存在，请检查Docker及setup.log；现有数据保留。'
        }
        if ($watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
            throw "数据库在${TimeoutSeconds}秒内仍未就绪，可能正在恢复。请查看setup.log及数据库日志后重试；不要删除数据卷。"
        }
        if ($watch.Elapsed.TotalSeconds -ge $nextNotice) {
            & $Log "Waiting for database SQL (state: $status); existing volume preserved."
            $nextNotice = $watch.Elapsed.TotalSeconds + 15
        }
        Start-Sleep -Seconds $PollSeconds
    }
}
