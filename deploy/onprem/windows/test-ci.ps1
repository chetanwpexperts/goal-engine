$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$location = Get-DBabaLocation
$base = 'http://127.0.0.1:8765'
$rows = [ordered]@{}
function Check($Name, [scriptblock]$Body) {
    try { & $Body; $rows[$Name] = 'PASS' }
    catch { $rows[$Name] = 'FAIL'; Write-Host "Failed: $Name" }
}
$env:DBABA_PASSWORD = [Guid]::NewGuid().ToString('N')
Write-Host "::add-mask::$env:DBABA_PASSWORD"
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
try {
    if ((Test-Path $location.Root) -or (Get-ScheduledTask -TaskName $location.Task -ErrorAction SilentlyContinue)) {
        throw 'Expected a clean runner.'
    }
    Check 'Install' {
        & (Join-Path $PSScriptRoot 'install.ps1') -NonInteractive -Port 8765
    }
    Check 'Health (60 seconds)' {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        do {
            try {
                $response = Invoke-RestMethod "$base/api/health" -TimeoutSec 2
                if ($response.ok -eq $true) { return }
            } catch {}
            Start-Sleep -Milliseconds 250
        } while ($timer.Elapsed.TotalSeconds -lt 58)
        throw 'Health timeout'
    }
    Check 'Loopback only' {
        $listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen)
        if (!$listeners.Count -or @($listeners | Where-Object LocalAddress -ne '127.0.0.1').Count) { throw 'Invalid listener' }
    }
    Check 'Login and session cookie' {
        $response = Invoke-WebRequest "$base/api/login" -Method Post -ContentType 'application/json' -Body (@{password=$env:DBABA_PASSWORD} | ConvertTo-Json -Compress) -WebSession $session -TimeoutSec 5
        if ($response.StatusCode -ne 200 -or !($session.Cookies.GetCookies([uri]$base)['dbaba_session'])) { throw 'Login failed' }
    }
    Check 'Authenticated state' {
        $response = Invoke-WebRequest "$base/api/state" -WebSession $session -TimeoutSec 5
        if ($response.StatusCode -ne 200) { throw 'State failed' }
    }
    Check 'Wrong password rejected' {
        $response = Invoke-WebRequest "$base/api/login" -Method Post -ContentType 'application/json' -Body '{"password":"wrong-password"}' -SkipHttpErrorCheck -TimeoutSec 5
        if ($response.StatusCode -ne 401) { throw 'Expected 401' }
    }
    Check 'Uninstall and verify removal' {
        & (Join-Path $PSScriptRoot 'uninstall.ps1') -NonInteractive
        if ((Test-Path $location.Root) -or (Get-ScheduledTask -TaskName $location.Task -ErrorAction SilentlyContinue) -or (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) { throw 'Removal incomplete' }
    }
} finally {
    Remove-Item Env:DBABA_PASSWORD -ErrorAction SilentlyContinue
    $summary = @('## Windows on-premises smoke test', '', '| Step | Result |', '| --- | --- |')
    foreach ($name in $rows.Keys) { $summary += "| $name | $($rows[$name]) |" }
    $summary | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
}
if ($rows.Count -ne 7 -or $rows.Values -contains 'FAIL') { exit 1 }
