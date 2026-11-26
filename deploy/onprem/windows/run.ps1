param([Parameter(Mandatory=$true)][string]$Root)
. (Join-Path $Root 'release\deploy\onprem\windows\common.ps1')
Set-DBabaEnvironment $Root
$port = [int](Get-Content -Raw -LiteralPath (Join-Path $Root 'dbaba.json') | ConvertFrom-Json).port
Set-Location (Join-Path $Root 'release')
$python = Join-Path $Root 'release\.venv\Scripts\python.exe'
if (Test-Path 'app\local.py') {
    & $python -m app.local serve --port $port
} else {
    Write-Host "This release's screen has no password protection yet; reach it only through an SSH tunnel."
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $port
}
exit $LASTEXITCODE
