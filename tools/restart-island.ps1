param()
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$logs = Join-Path $root 'logs'
$pidPath = Join-Path $logs 'pids.json'
$saved = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$islandId = [int]$saved.islandserver
if (-not $islandId) { throw 'No saved island PID; use Play.bat to start services.' }
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$islandId"
$python = (Get-Command python).Source
if (-not $process -or $process.ExecutablePath -ine $python -or
    $process.CommandLine -notmatch 'run\.py\s+--port\s+8791\s+--hunt\s+Adventure_IslandA_D24\b') {
    throw 'Island identity could not be verified. No process was stopped.'
}
$matchingLog = Get-ChildItem $logs -Filter 'islandserver_*.log' | Where-Object {
    [Math]::Abs(($_.CreationTime - $process.CreationDate).TotalSeconds) -lt 5
}
if (-not $matchingLog) { throw 'Island startup log does not match the saved process. No process was stopped.' }
$key = (Get-Content (Join-Path $root 'Backend\data\gameserver.key') -Raw).Trim()
$headers = @{ 'x-phoenixrising-gameserver-apikey' = $key }
$info = @{ gameMode='ISLAND'; huntId='Adventure_IslandA_D24'; host='127.0.0.1'; port=8791 }
Invoke-RestMethod 'http://127.0.0.1:3001/instances/unregister' -Method Post -Headers $headers -ContentType 'application/json' -Body ($info | ConvertTo-Json) | Out-Null
Stop-Process -Id $islandId -ErrorAction Stop
Wait-Process -Id $islandId -Timeout 10 -ErrorAction SilentlyContinue
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$env:MPGS_TRACE_ACTOR_RX = '1'
$env:MPGS_TEST_INVENTORY_V2_ALL = '1'
$env:MPGS_TEST_INVENTORY_DELAY_MS = '5000'
$new = Start-Process -FilePath $python -ArgumentList @('-u','run.py','--port','8791','--hunt','Adventure_IslandA_D24','--template','world\islands\Adventure_IslandA_D24.json') -WorkingDirectory (Join-Path $root 'GameServer') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "islandserver_$stamp.log") -RedirectStandardError (Join-Path $logs "islandserver_$stamp.err.log")
$saved.islandserver = $new.Id
$saved | ConvertTo-Json | Set-Content -LiteralPath $pidPath
$deadline = (Get-Date).AddSeconds(20)
do {
    if ($new.HasExited) { throw "Island exited; inspect logs\islandserver_$stamp.err.log" }
    $instances = Invoke-RestMethod 'http://127.0.0.1:3001/instances' -Headers $headers -TimeoutSec 2
    if ($instances.islands.Adventure_IslandA_D24.port -eq 8791) {
        Write-Host "Island ready (PID $($new.Id)). Join the hunt again." -ForegroundColor Green
        return
    }
    Start-Sleep -Milliseconds 300
} until ((Get-Date) -ge $deadline)
throw 'Island registration timed out; inspect the latest islandserver log.'
