$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$location = Get-DBabaLocation
$root = $location.Root
Assert-RegularPath $root
if (-not (Test-Path $root)) { Write-Host 'DBaba is already removed.'; exit 0 }
if (-not (Test-Path (Join-Path $root '.dbaba-install'))) { throw 'Please use the original DBaba installation directory.' }
if ((Read-Host "Remove DBaba from $root, including saved settings and data? [y/N]") -notmatch '^(y|yes)$') { exit 0 }
$lock = [IO.File]::Open((Join-Path $root '.update-lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $config = Join-Path $root 'dbaba.json'
    Assert-RegularPath $config
    if (Test-Path $config) {
        $saved = Get-Content -Raw -LiteralPath $config | ConvertFrom-Json
        if ($saved.database.user -eq 'dbaba_ro') {
            $python = Join-Path $root 'release\.venv\Scripts\python.exe'
            if (-not (Test-Path $python)) {
                $python = (Get-Command python.exe -ErrorAction Stop).Source
            }
            Invoke-Python $python @((Join-Path $root 'configure.py'), $config, 'drop')
        }
    }
    if (Get-ScheduledTask -TaskName $location.Task -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $location.Task
        Unregister-ScheduledTask -TaskName $location.Task -Confirm:$false
    }
    Set-Location $env:TEMP
    # Remove release junctions without following them into the persistent data.
    foreach ($release in @('release','.release-new','.release-old')) {
        $junction = Join-Path $root "$release\data"
        if ((Test-Path $junction) -and ((Get-Item -LiteralPath $junction -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            [IO.Directory]::Delete($junction)
        }
    }
} finally { $lock.Dispose() }
Remove-Item -LiteralPath $root -Recurse -Force
Write-Host 'DBaba service, files and local data are removed.'
