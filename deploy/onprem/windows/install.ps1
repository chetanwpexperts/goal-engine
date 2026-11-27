param([string]$Source = $env:DBABA_SRC)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$location = Get-DBabaLocation
# Boot-time service needs a Python installation accessible to Local Service.
$root = $location.Root
$task = $location.Task
$python = $null
foreach ($candidate in @('python.exe','python3.exe','py.exe')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $prefix = if ($candidate -eq 'py.exe') { @('-3') } else { @() }
        $found = & $cmd.Source @prefix -c 'import sys,venv,ensurepip; assert sys.version_info >= (3,11); print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $python = $found; break }
    }
}
if (-not $python) { throw 'Python 3.11 or newer is needed. Run: winget install -e --id Python.Python.3.11' }
if ($location.Admin -and $python.StartsWith($env:USERPROFILE + '\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'For boot-time startup, install Python for all users first (winget install -e --id Python.Python.3.11 --scope machine).'
}
if (-not $Source) { $Source = Join-Path $PSScriptRoot '..\..\..' }
$Source = (Resolve-Path -LiteralPath $Source).Path
if (-not (Test-Path (Join-Path $Source 'app\main.py'))) { throw 'Please supply a complete release directory with -Source.' }
Assert-RegularPath $root
if ((Test-Path $root) -and -not (Test-Path (Join-Path $root '.dbaba-install'))) {
    throw 'The installation directory already exists. Please review it before installing.'
}
if (-not (Test-Path $root)) {
    New-Item -ItemType Directory -Path $root | Out-Null
    Protect-DBabaRoot $root $location.Account
    New-Item -ItemType File -Path (Join-Path $root '.dbaba-install') | Out-Null
}
$lock = [IO.File]::Open((Join-Path $root '.update-lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    foreach ($name in @('data','dbaba.json','release','.release-new','.release-old','uninstall.ps1','common.ps1','configure.py')) {
        Assert-RegularPath (Join-Path $root $name)
    }
    if (Test-Path (Join-Path $root '.release-old')) { throw 'Review the saved previous release before retrying.' }
    if (-not (Test-Path (Join-Path $root 'data'))) { New-Item -ItemType Directory -Path (Join-Path $root 'data') | Out-Null }
    $config = Join-Path $root 'dbaba.json'
    if (Test-Path $config) {
        $port = [int](Get-Content -Raw -LiteralPath $config | ConvertFrom-Json).port
        Write-Host 'Keeping your existing configuration and data.'
    } else {
        $answer = Read-Host 'Screen port [8765]'
        $port = if ($answer) { [int]$answer } else { 8765 }
        if ($port -lt 1024 -or $port -gt 65535) { throw 'Choose a port from 1024 to 65535.' }
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw 'Choose an available port.' }
        Invoke-Python $python @((Join-Path $PSScriptRoot 'configure.py'), $config, 'create', '--port', "$port")
    }
    if ($port -lt 1024 -or $port -gt 65535) { throw 'Choose a port from 1024 to 65535.' }
    $next = Join-Path $root '.release-new'
    $active = Join-Path $root 'release'
    Remove-DBabaRelease $next
    New-Item -ItemType Directory -Path $next | Out-Null
    # Copy only regular release files. Never import saved settings or data.
    $copy = @'
import shutil, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
ignore = {".git", ".env", ".venv", "venv", "data", "dbaba.json", "__pycache__", ".pytest_cache", "node_modules"}
def copy(src, dst):
    for item in src.iterdir():
        if item.name in ignore or item.name.startswith(".env."):
            continue
        if item.is_symlink() or getattr(item.lstat(), "st_file_attributes", 0) & 1024:
            raise SystemExit("Please use a release with regular files and directories.")
        out = dst / item.name
        if item.is_dir():
            out.mkdir()
            copy(item, out)
        elif item.is_file():
            shutil.copyfile(item, out)
copy(source, target)
'@
    if ($next.StartsWith($Source.TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Choose a source outside the installation directory.' }
    $copyFile = Join-Path $next '.copy-release.py'
    [IO.File]::WriteAllText($copyFile, $copy)
    Invoke-Python $python @($copyFile,$Source,$next)
    Remove-Item -LiteralPath $copyFile
    Invoke-Python $python @('-m','venv',(Join-Path $next '.venv'))
    $venv = Join-Path $next '.venv\Scripts\python.exe'
    Invoke-Python $venv @('-m','pip','--isolated','install','--disable-pip-version-check','--no-input','-r',(Join-Path $next 'requirements.txt'))
    Push-Location $next
    try {
        Set-DBabaEnvironment $next -Check
        Invoke-Python $venv @('-c','import app.main')
        # Probe the staged package with the same interpreter used by the task.
        $hasLocal = $false
        try {
            & $venv -c 'import app.local' 2>$null | Out-Null
            $hasLocal = ($LASTEXITCODE -eq 0)
        } catch { $hasLocal = $false }
    } finally { Pop-Location }
    foreach ($name in @('data','dbaba.json')) {
        $path = Join-Path $next $name
        if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    # A junction keeps relative application data paths stable across updates.
    New-Item -ItemType Junction -Path (Join-Path $next 'data') -Target (Join-Path $root 'data') | Out-Null
    foreach ($name in @('uninstall.ps1','common.ps1','configure.py')) {
        if (-not (Test-Path (Join-Path $root $name))) {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $root $name)
        }
    }
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $active 'deploy\onprem\windows\run.ps1') + '" -Root "' + $root + '"')
    if ($location.Admin) {
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId $location.Account -LogonType ServiceAccount
    } else {
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $location.Account
        $principal = New-ScheduledTaskPrincipal -UserId $location.Account -LogonType Interactive
    }
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $stop = { Stop-DBabaTask $task $port }
    $start = {
        Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $task
    }
    $health = { Wait-DBabaHealth $port $task }
    $remove = { Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue }
    Switch-DBabaRelease $root $stop $start $health $remove
    foreach ($name in @('uninstall.ps1','common.ps1','configure.py')) {
        Copy-Item -LiteralPath (Join-Path $active "deploy\onprem\windows\$name") -Destination (Join-Path $root $name) -Force
    }
    if (-not $hasLocal) {
        Write-Host "This release's screen has no password protection yet; reach it only through an SSH tunnel."
    }
    if ($location.Admin) { Write-Host 'DBaba starts automatically at boot.' }
    else { Write-Host 'DBaba starts when you sign in; ask an administrator for boot-time start.' }
    Write-Host "DBaba is available at http://127.0.0.1:$port. No inbound firewall rule was added."
} finally { $lock.Dispose() }
