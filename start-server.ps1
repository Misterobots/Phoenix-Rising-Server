# Dauntless - Phoenix Rising: start the backend (HTTP 3001, WebSocket 3000, HTTPS 443) and the game server (UDP).
#
# Prerequisites (see README.md): Python 3.11+ with `pip install -r Backend\requirements.txt`, and
# tools\setup-local.ps1 run once (elevated) after the first start, for the hosts entries and certificate.
#
#   .\start-server.ps1            start both (logs in .\logs)
#   .\start-server.ps1 -Stop      stop what this script started

param([switch]$Stop, [int]$GamePort = 8790)

$Root = $PSScriptRoot
$Logs = Join-Path $Root 'logs'
$PidFile = Join-Path $Logs 'pids.json'
New-Item -ItemType Directory -Force $Logs | Out-Null

if ($Stop) {
    if (Test-Path $PidFile) {
        (Get-Content $PidFile | ConvertFrom-Json).PSObject.Properties | ForEach-Object {
            Stop-Process -Id $_.Value -Force -ErrorAction SilentlyContinue
            Write-Host "stopped $($_.Name) ($($_.Value))"
        }
        Remove-Item $PidFile
    }
    return
}

function Start-Part($Name, $Dir, $ArgList, $Env = @{}) {
    foreach ($k in $Env.Keys) { Set-Item "Env:$k" $Env[$k] }
    $p = Start-Process -FilePath 'python' -ArgumentList $ArgList -WorkingDirectory (Join-Path $Root $Dir) `
        -RedirectStandardOutput (Join-Path $Logs "$Name.log") -RedirectStandardError (Join-Path $Logs "$Name.err.log") `
        -WindowStyle Hidden -PassThru
    Write-Host ("{0,-12} pid {1}" -f $Name, $p.Id)
    return $p.Id
}

$pids = [ordered]@{}
$pids.backend = Start-Part 'backend' 'Backend' @('-u', '-m', 'phoenix_backend')
Start-Sleep -Seconds 2     # first start creates Backend\data (game-server key, certificates) for the game server
# Game server: full inventory sync for the stock client; test helpers are opt-in (README "Game server options").
$pids.gameserver = Start-Part 'gameserver' 'GameServer' @('-u', 'run.py', '--port', "$GamePort") @{
    MPGS_TEST_INVENTORY_V2_ALL = '1'; MPGS_TEST_INVENTORY_DELAY_MS = '5000'
}
$pids | ConvertTo-Json | Set-Content $PidFile
Write-Host "Logs: $Logs. Launch the client with .\tools\launch-client.ps1 -GameDir <Dauntless install folder>"
