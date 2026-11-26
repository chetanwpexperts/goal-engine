$ErrorActionPreference = 'Stop'
function Get-DBabaLocation {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $admin = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $sid = $identity.User.Value
    if ($admin) {
        return @{ Root = (Join-Path $env:ProgramData 'DBaba'); Task = 'DBaba'; Account = 'S-1-5-19'; Admin = $true }
    }
    return @{ Root = (Join-Path $env:LOCALAPPDATA 'DBaba'); Task = "DBaba-$sid"; Account = $sid; Admin = $false }
}
function Assert-RegularPath($Path) {
    if (Test-Path -LiteralPath $Path) {
        if ((Get-Item -Force -LiteralPath $Path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Please use a regular path: $Path"
        }
    }
}
function Protect-DBabaRoot($Path, $Account) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($Account, 'S-1-5-32-544') | Select-Object -Unique) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl',
            'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Invoke-Python($Python, [string[]]$Arguments) {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Python could not complete the requested step.' }
}
function Set-DBabaEnvironment($Root, [switch]$Check) {
    $env:SITE_MODE = 'open'
    $env:ENGINE_MODE = 'ml'
    $env:AGENT_SCHEDULER = if ($Check) { 'off' } else { 'on' }
    $env:DBABA_LOCAL = '1'
    $env:UVICORN_HOST = '127.0.0.1'
    $env:COOKIE_SECURE = 'false'
    $env:EXPOSE_INTERNALS = '0'
    $env:GEMINI_API_KEY = ''
    $env:GROQ_API_KEY = ''
    $env:AI_PROVIDER_ORDER = ''
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONPATH = ''
    $env:DBABA_CONFIG = Join-Path $Root 'dbaba.json'
    $env:DATABASE_URL = if ($Check) { 'sqlite:///:memory:' } else {
        'sqlite:///' + ((Join-Path $Root 'data\dbaba.db') -replace '\\','/')
    }
}
function Wait-DBabaHealth($Port, $Task, $Seconds = 180) {
    $until = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        try {
            $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
            if ($listeners.Count -gt 0 -and @($listeners | Where-Object LocalAddress -ne '127.0.0.1').Count -eq 0 -and
                (Get-ScheduledTask -TaskName $Task).State -eq 'Running') {
                $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/api/health")
                $request.Proxy = $null
                $request.Timeout = 2000
                $request.ReadWriteTimeout = 2000
                $response = $request.GetResponse()
                try {
                    $reader = [IO.StreamReader]::new($response.GetResponseStream())
                    try { $result = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
                    if ($result.ok -eq $true) { return $true }
                } finally { $response.Dispose() }
            }
        } catch {}
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $until)
    return $false
}
function Stop-DBabaTask($Task, $Port) {
    if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $Task
    }
    $until = [DateTime]::UtcNow.AddSeconds(30)
    while (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        if ([DateTime]::UtcNow -gt $until) { throw 'The saved port is still in use. Your release is kept.' }
        Start-Sleep -Milliseconds 500
    }
}
function Remove-DBabaRelease($Path) {
    Assert-RegularPath $Path
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $junction = Join-Path $Path 'data'
    if ((Test-Path -LiteralPath $junction) -and ((Get-Item -LiteralPath $junction -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        [IO.Directory]::Delete($junction)
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
}
function Switch-DBabaRelease($Root, [scriptblock]$Stop, [scriptblock]$Start, [scriptblock]$Health, [scriptblock]$RemoveTask) {
    $active = Join-Path $Root 'release'
    $next = Join-Path $Root '.release-new'
    $old = Join-Path $Root '.release-old'
    foreach ($path in @($active,$next,$old)) { Assert-RegularPath $path }
    if (Test-Path $old) { throw 'Please review the saved previous release before retrying.' }
    $had = Test-Path $active
    $moved = $false
    $activated = $false
    try {
        & $Stop
        if ($had) { Move-Item -LiteralPath $active -Destination $old; $moved = $true }
        Move-Item -LiteralPath $next -Destination $active
        $activated = $true
        & $Start
        if (-not (& $Health)) { throw 'The new release did not pass its health check.' }
    } catch {
        $failure = $_
        if ($activated) {
            & $Stop
            Move-Item -LiteralPath $active -Destination $next
        }
        if ($moved) { Move-Item -LiteralPath $old -Destination $active }
        if ($had) {
            & $Start
            if (-not (& $Health)) { throw 'The previous release is restored but is not healthy. Please review the saved files.' }
            Write-Host 'The previous release is running again.'
        } else { & $RemoveTask }
        throw $failure
    }
    if ($moved) { Remove-DBabaRelease $old }
}
