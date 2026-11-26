BeforeAll {
    . (Join-Path $PSScriptRoot '../../deploy/onprem/windows/common.ps1')
}
Describe 'DBaba release update' {
    BeforeEach {
        $root = Join-Path $TestDrive ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $root | Out-Null
        foreach ($name in @('release','.release-new','data')) {
            New-Item -ItemType Directory -Path (Join-Path $root $name) | Out-Null
        }
        Set-Content (Join-Path $root 'release/version') 'old'
        Set-Content (Join-Path $root '.release-new/version') 'new'
        Set-Content (Join-Path $root 'dbaba.json') 'saved config'
        Set-Content (Join-Path $root 'data/saved') 'saved data'
    }
    It 'activates a healthy release without touching saved paths' {
        Switch-DBabaRelease $root {} {} { $true } {}
        Get-Content (Join-Path $root 'release/version') | Should -Be 'new'
        Test-Path (Join-Path $root '.release-old') | Should -BeFalse
        Get-Content (Join-Path $root 'dbaba.json') | Should -Be 'saved config'
        Get-Content (Join-Path $root 'data/saved') | Should -Be 'saved data'
    }
    It 'restores the prior release on failed health check' {
        $health = { (Get-Content (Join-Path $root 'release/version')) -eq 'old' }
        { Switch-DBabaRelease $root {} {} $health {} } | Should -Throw
        Get-Content (Join-Path $root 'release/version') | Should -Be 'old'
        Get-Content (Join-Path $root 'dbaba.json') | Should -Be 'saved config'
        Get-Content (Join-Path $root 'data/saved') | Should -Be 'saved data'
    }
}
Describe 'DBaba configuration' {
    It 'uses the Linux scrypt format' {
        $helper = Join-Path $PSScriptRoot '../../deploy/onprem/windows/configure.py'
        $code = 'import runpy,sys,hashlib; c=runpy.run_path(sys.argv[1])["new_config"](8765,"test password"); p=c["screen_password"]; assert c["version"]==1 and c["port"]==8765 and c["database"] is None; assert len(bytes.fromhex(p["salt"]))==16 and len(bytes.fromhex(p["hash"]))==64; assert (p["n"],p["r"],p["p"])==(16384,8,1); assert hashlib.scrypt(b"test password",salt=bytes.fromhex(p["salt"]),n=16384,r=8,p=1,dklen=64).hex()==p["hash"]'
        & python -c $code $helper
        $LASTEXITCODE | Should -Be 0
    }
}
