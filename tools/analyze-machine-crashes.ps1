# Read existing crash dumps only. Never starts/stops game services, changes
# dump permissions, enables Driver Verifier, or schedules a reboot.
[CmdletBinding()]
param([switch]$FullDump)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell as Administrator and run this script again. Administrator access is required to read Windows crash dumps.'
}
$root = Split-Path $PSScriptRoot -Parent
$output = Join-Path $root ('logs\crash-analysis-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$package = Get-AppxPackage -Name Microsoft.WinDbg | Select-Object -First 1
$debugger = if ($package) { Join-Path $package.InstallLocation 'amd64\cdb.exe' }
if (-not $debugger -or -not (Test-Path -LiteralPath $debugger)) {
    throw 'Microsoft WinDbg amd64 debugger was not found for this administrator account.'
}
$dumps = if ($FullDump) { @('MEMORY.DMP') } else { @('101926-23906-01.dmp', '101926-11734-01.dmp') }
foreach ($name in $dumps) {
    $path = if ($FullDump) { Join-Path $env:SystemRoot $name } else { Join-Path $env:SystemRoot ('Minidump\' + $name) }
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing crash dump: $path" }
}
New-Item -ItemType Directory -Path $output | Out-Null
$symbols = Join-Path $env:LOCALAPPDATA 'PhoenixCrashSymbols'
foreach ($name in $dumps) {
    $path = if ($FullDump) { Join-Path $env:SystemRoot $name } else { Join-Path $env:SystemRoot ('Minidump\' + $name) }
    $log = Join-Path $output ($name + '.txt')
    Write-Host "Analyzing $name; first-time Microsoft symbol downloads may take several minutes."
    $commands = '!analyze -v; .bugcheck; kv; lm t n; q'
    if ($FullDump) {
        $commands = '!analyze -v; .bugcheck; !thread; kv; lmvm EasyAntiCheat_EOS; !wmitrace.loggers; !vm; !blackboxpnp; q'
    }
    & $debugger -z $path -y "srv*$symbols*https://msdl.microsoft.com/download/symbols" -logo $log -c $commands
    if ($LASTEXITCODE -ne 0) { throw "Debugger failed for $name (exit $LASTEXITCODE). Inspect $log" }
    if (-not (Select-String -LiteralPath $log -Pattern 'BUGCHECK_CODE:|Bugcheck Analysis' -Quiet)) {
        throw "No completed bugcheck analysis found in $log"
    }
}
Write-Host "Analysis saved to $output" -ForegroundColor Green
Write-Host 'No game services were started or stopped. Tell Codex the analysis is ready.'
