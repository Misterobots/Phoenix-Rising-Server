# Dauntless - Phoenix Rising: start the backend (HTTP 3001, WebSocket 3000, HTTPS 443) and the game server (UDP).
#
# Prerequisites (see README.md): Python 3.11+ with `pip install -r Backend\requirements.txt`, and
# tools\setup-local.ps1 run once (elevated) after the first start, for the hosts entries and certificate.
#
#   .\start-server.ps1            start both (logs in .\logs)
#   .\start-server.ps1 -Stop      stop what this script started

param([switch]$Stop, [int]$GamePort = 8790, [int]$IslandPort = 8791, [string]$IslandHunt = 'Adventure_IslandA_D24')

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Logs = Join-Path $Root 'logs'
$PidFile = Join-Path $Logs 'pids.json'
New-Item -ItemType Directory -Force $Logs | Out-Null

function Test-OwnedPort($Id, $Port, $Protocol) {
    if ($Protocol -eq 'tcp') { $rows = netstat -ano -p tcp 2>$null; $state = 'LISTENING' } else { $rows = netstat -ano -p udp 2>$null; $state = '\*:\*' }
    $escapedId = [regex]::Escape([string]$Id)
    $escapedPort = [regex]::Escape([string]$Port)
    return [bool]($rows | Where-Object { $_ -match ":$escapedPort\s" -and $_ -match $state -and $_ -match "\s$escapedId\s*$" })
}

function Test-TcpConnect($Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(1500)) { return $false }
        $client.EndConnect($pending)
        return $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

# Some Windows installations omit live loopback listeners from netstat. Require
# this process's startup log and command line as well as a successful connection.
function Test-BackendReady($Id, $Port) {
    $proc = Get-Process -Id $Id -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.ProcessName -ne 'python') { return $false }
    $info = Get-CimInstance Win32_Process -Filter "ProcessId=$Id"
    if ($info.CommandLine -notmatch '-m\s+phoenix_backend\b') { return $false }
    $log = Get-ChildItem -LiteralPath $Logs -Filter 'backend_*.err.log' | Where-Object {
        [Math]::Abs(($_.CreationTime - $proc.StartTime).TotalSeconds) -lt 5
    } | Select-Object -First 1
    if (-not $log) { return $false }
    $started = Select-String -LiteralPath $log.FullName -Pattern "listening on 127\.0\.0\.1:${Port}(\s|$)" -Quiet
    return ($started -and (Test-TcpConnect $Port))
}

if ($Stop) {
    if (Test-Path $PidFile) {
        $saved = Get-Content $PidFile -Raw | ConvertFrom-Json
        foreach ($name in @('islandserver', 'gameserver', 'backend')) {
            $id = $saved.$name
            if ($id) {
                $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
                $owned = if ($name -eq 'backend') {
                    (Test-OwnedPort $id 3001 'tcp') -or (Test-OwnedPort $id 443 'tcp')
                } elseif ($name -eq 'islandserver') {
                    Test-OwnedPort $id $IslandPort 'udp'
                } else { Test-OwnedPort $id $GamePort 'udp' }
                if ($proc -and $proc.ProcessName -eq 'python' -and $owned) {
                    try {
                        Stop-Process -Id $id -Force -ErrorAction Stop
                    } catch {
                        Start-Process taskkill -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList @('/F', '/PID', "$id")
                    }
                    Wait-Process -Id $id -Timeout 10 -ErrorAction SilentlyContinue
                    Write-Host "stopped $name ($id)"
                } elseif ($proc) {
                    Write-Warning "PID $id no longer belongs to this package's $name; leaving it untouched"
                } else {
                    Write-Host "$name already stopped"
                }
            }
        }
        Remove-Item $PidFile -Force
    } else {
        Write-Host 'No saved server PIDs; server processes were not started by this script.'
    }
    # Routing is needed only while this local server is in use. Remove its hosts
    # overrides and trust anchor so Epic Launcher can reach Epic again.
    $setup = Join-Path $Root 'tools\setup-local.ps1'
    try {
        Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -eq 'CN=Phoenix Rising Local CA' } | Remove-Item
        $cleanup = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $setup, '-Remove')
        if ($cleanup.ExitCode -ne 0) { throw "Elevated cleanup exited with code $($cleanup.ExitCode)." }
        Write-Host 'Removed Phoenix Rising hosts routing and local CA.'
    } catch {
        Write-Warning "Could not remove system routing automatically. Run elevated: $setup -Remove"
        throw
    }
    return
}

function Running($id, $port, $protocol) {
    if (-not $id) { return $false }
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    return [bool]($proc -and $proc.ProcessName -eq 'python' -and (Test-OwnedPort $id $port $protocol))
}

function Assert-PortAvailable($Port, $Protocol) {
    $rows = netstat -ano -p $Protocol 2>$null
    foreach ($row in $rows) {
        $fields = $row.Trim() -split '\s+'
        if ($fields.Count -lt 4 -or $fields[0] -ine $Protocol) { continue }
        if ($fields[1] -notmatch ":${Port}$") { continue }
        if ($Protocol -eq 'tcp' -and $fields[3] -ne 'LISTENING') { continue }
        throw "Cannot start: $Protocol port $Port is already owned by PID $($fields[-1]). No verified saved process exists for this service. Resolve the existing service before retrying."
    }
}

function Wait-OwnedPort($Id, $Port, $Protocol, $Name) {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        if (-not (Get-Process -Id $Id -ErrorAction SilentlyContinue)) {
            throw "$Name (PID $Id) exited during startup; inspect the newest ${Name}_*.err.log in $Logs"
        }
        if (Test-OwnedPort $Id $Port $Protocol) { return }
        if ($Name -eq 'backend' -and (Test-BackendReady $Id $Port)) { return }
        Start-Sleep -Milliseconds 250
    } until ((Get-Date) -ge $deadline)
    throw "$Name (PID $Id) did not bind $Protocol port $Port; inspect its log in $Logs"
}

function Start-Part($Name, $Dir, $ArgList, $Env = @{}) {
    foreach ($k in $Env.Keys) { Set-Item "Env:$k" $Env[$k] }
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $p = Start-Process -FilePath 'python' -ArgumentList $ArgList -WorkingDirectory (Join-Path $Root $Dir) `
        -RedirectStandardOutput (Join-Path $Logs "${Name}_$stamp.log") -RedirectStandardError (Join-Path $Logs "${Name}_$stamp.err.log") `
        -WindowStyle Hidden -PassThru
    Write-Host ("{0,-12} started, pid {1}" -f $Name, $p.Id)
    return $p.Id
}

# Start only what is not already running (a restart after one part stopped keeps the other).
$pids = [ordered]@{ backend = $null; gameserver = $null; islandserver = $null }
if (Test-Path $PidFile) {
    $old = Get-Content $PidFile | ConvertFrom-Json
    $pids.backend = $old.backend; $pids.gameserver = $old.gameserver; $pids.islandserver = $old.islandserver
}
$backendRunning = Running $pids.backend 3001 'tcp'
if (-not $backendRunning -and $pids.backend) { $backendRunning = Test-BackendReady $pids.backend 3001 }
$gameRunning = Running $pids.gameserver $GamePort 'udp'
$islandRunning = Running $pids.islandserver $IslandPort 'udp'
# Check all services before starting any; do not launch duplicates when PID state is lost.
if (-not $backendRunning) {
    foreach ($port in @(3001, 3000, 443)) { Assert-PortAvailable $port 'tcp' }
}
if (-not $gameRunning) { Assert-PortAvailable $GamePort 'udp' }
if (-not $islandRunning) { Assert-PortAvailable $IslandPort 'udp' }
if ($backendRunning) { Write-Host "backend      running, pid $($pids.backend)" }
else {
    $pids.backend = Start-Part 'backend' 'Backend' @('-u', '-m', 'phoenix_backend')
    $pids | ConvertTo-Json | Set-Content $PidFile
    foreach ($port in @(3001, 3000, 443)) { Wait-OwnedPort $pids.backend $port 'tcp' 'backend' }
}
if ($gameRunning) { Write-Host "gameserver   running, pid $($pids.gameserver)" }
else {
    # Full inventory sync for the stock client; test helpers are opt-in (README "Game server options").
    $pids.gameserver = Start-Part 'gameserver' 'GameServer' @('-u', 'run.py', '--port', "$GamePort") @{
        MPGS_TEST_INVENTORY_V2_ALL = '1'; MPGS_TEST_INVENTORY_DELAY_MS = '5000'
    }
}
$pids | ConvertTo-Json | Set-Content $PidFile
Wait-OwnedPort $pids.gameserver $GamePort 'udp' 'gameserver'

$islandTemplate = Join-Path 'world\islands' "$IslandHunt.json"
if ($islandRunning) { Write-Host "islandserver running, pid $($pids.islandserver)" }
else {
    $pids.islandserver = Start-Part 'islandserver' 'GameServer' @(
        '-u', 'run.py', '--port', "$IslandPort", '--hunt', $IslandHunt, '--template', $islandTemplate
    ) @{
        MPGS_TRACE_ACTOR_RX = '1'; MPGS_TEST_INVENTORY_V2_ALL = '1'; MPGS_TEST_INVENTORY_DELAY_MS = '5000'
    }
}
$pids | ConvertTo-Json | Set-Content $PidFile
Wait-OwnedPort $pids.islandserver $IslandPort 'udp' 'islandserver'

# Make the local HTTPS routing active only once the backend has bound 443 and
# generated its CA. setup-local is idempotent and requires elevation for hosts.
$deadline = (Get-Date).AddSeconds(30)
do {
    $backendReady = $false
    try { $backendReady = (Invoke-WebRequest 'http://127.0.0.1:3001/' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch {}
    # A direct connect is the authoritative readiness check. Get-NetTCPConnection
    # can temporarily omit a newly bound loopback listener on this Windows host.
    $tlsReady = Test-TcpConnect 443
    if (-not ($backendReady -and $tlsReady)) { Start-Sleep -Milliseconds 500 }
} until (($backendReady -and $tlsReady) -or (Get-Date) -ge $deadline)
if (-not $backendReady -or -not $tlsReady) { throw "Backend HTTP/TLS did not become ready; inspect the newest backend log in $Logs" }
$gsKey = (Get-Content (Join-Path $Root 'Backend\data\gameserver.key') -Raw).Trim()
$gsHeaders = @{ 'x-phoenixrising-gameserver-apikey' = $gsKey }
Invoke-RestMethod 'http://127.0.0.1:3001/instances/register' -Method Post -Headers $gsHeaders -ContentType 'application/json' -Body (@{
    gameMode='CITY'; huntId=''; host='127.0.0.1'; port=$GamePort;
    map='/Game/Maps/ramsgate/ramsgate_01_persistent';
    welcomeGameMode='/Game/Blueprints/GameMode/BPGM_City.BPGM_City_C'
} | ConvertTo-Json) | Out-Null
Invoke-RestMethod 'http://127.0.0.1:3001/instances/register' -Method Post -Headers $gsHeaders -ContentType 'application/json' -Body (@{
    gameMode='ISLAND'; huntId=$IslandHunt; host='127.0.0.1'; port=$IslandPort;
    map='/Game/Maps/islands/adventure/Moss_Triforce/adventure_moss_triforce';
    welcomeGameMode='/Game/Blueprints/GameMode/BPGM_ArchonExploration_Tutorial.BPGM_ArchonExploration_Tutorial_C'
} | ConvertTo-Json) | Out-Null
$setup = Join-Path $Root 'tools\setup-local.ps1'
$setupResult = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $setup)
if ($setupResult.ExitCode -ne 0) { throw "Local hosts/certificate setup exited with code $($setupResult.ExitCode)." }
# UAC may use a different administrator account. Trust the CA for the invoking
# player too, since that is the account running the client.
$certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new((Join-Path $Root 'Backend\data\tls\ca.pem'))
$certificatePath = Join-Path $Root 'Backend\data\tls\ca.pem'
$derPath = [IO.Path]::ChangeExtension($certificatePath, '.cer')
[IO.File]::WriteAllBytes($derPath, $certificate.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
$hostsText = Get-Content "$env:SystemRoot\System32\drivers\etc\hosts" -Raw
$trustedCa = Get-ChildItem Cert:\CurrentUser\Root,Cert:\LocalMachine\Root -ErrorAction SilentlyContinue | Where-Object { $_.Thumbprint -eq $certificate.Thumbprint }
if (-not $trustedCa) {
    Import-Certificate -FilePath $derPath -CertStoreLocation Cert:\CurrentUser\Root -ErrorAction Stop | Out-Null
    $trustedCa = Get-ChildItem Cert:\CurrentUser\Root,Cert:\LocalMachine\Root -ErrorAction SilentlyContinue | Where-Object { $_.Thumbprint -eq $certificate.Thumbprint }
}
if ($hostsText -notmatch 'phoenix-rising' -or -not $trustedCa) { throw 'Local routing setup did not install both the hosts entries and trusted CA.' }
Write-Host "Logs: $Logs"

