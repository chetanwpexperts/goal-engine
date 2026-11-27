param([Parameter(Mandatory=$true)][string]$Root)
. (Join-Path $Root 'release\deploy\onprem\windows\common.ps1')
Set-DBabaEnvironment $Root
$port = [int](Get-Content -Raw -LiteralPath (Join-Path $Root 'dbaba.json') | ConvertFrom-Json).port
Set-Location (Join-Path $Root 'release')
$python = Join-Path $Root 'release\.venv\Scripts\python.exe'
$hasLocal = $false
try {
    & $python -c 'import app.local' 2>$null | Out-Null
    $hasLocal = ($LASTEXITCODE -eq 0)
} catch { $hasLocal = $false }
if ($hasLocal) {
    & $python -m app.local serve --port $port --config (Join-Path $Root 'dbaba.json') --host 127.0.0.1
} else {
    Write-Host "This release's screen has no password protection yet; reach it only through an SSH tunnel."
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $port
}
exit $LASTEXITCODE
