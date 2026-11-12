$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopDir = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopDir '启动热点雷达.lnk'
$powerShell = (Get-Command pwsh.exe -ErrorAction Stop).Source
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powerShell
$shortcut.Arguments = '-NoLogo -NoProfile -WindowStyle Hidden -File "' +
    (Join-Path $PSScriptRoot 'start-desktop.ps1') + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = '启动HotRadar数据库、网页与采集进程，就绪后打开网页；复用已运行服务。'
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,14"
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Output "已创建：$shortcutPath"
