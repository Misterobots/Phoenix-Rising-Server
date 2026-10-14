# Dauntless - Phoenix Rising: one-click setup and play (Play.bat runs this).
#
# Every run: start the backend and game server (if not running) and launch the stock game client.
# First run also: install the Python packages, find the Dauntless 2.1.1 install, and (one administrator prompt)
# add the hosts entries and trust the local certificate.
#
#   .\deploy.ps1 [-GameDir <Dauntless folder>] [-Name <player name>]

param([string]$GameDir, [string]$Name)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Config = Join-Path $Root 'Backend\data\deploy.json'
$Sha211 = '934325ACECF64E19A01F70F72DCDCEBD68CFB12F11A812CD551C608AC16CEA34'
function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }

# 1. Python 3.11+
$py = Get-Command python -ErrorAction SilentlyContinue
$ver = if ($py) { & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } else { $null }
if (-not $ver -or [version]$ver -lt [version]'3.11') {
    Write-Host 'Python 3.11 or newer is required.' -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if ((Read-Host 'Install Python 3.12 now with winget? [Y/n]') -notmatch '^[nN]') {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements
            Write-Host 'Python installed. Close this window and run Play.bat again.'; exit 0
        }
    }
    throw 'Install Python 3.11+ from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run Play.bat again.'
}

# 2. Python packages
Step 'Checking Python packages'
& python -c "import aiohttp, cryptography" 2>$null
if ($LASTEXITCODE -ne 0) { & python -m pip install --user -q -r (Join-Path $Root 'Backend\requirements.txt') }

# 3. Saved settings, game folder
$saved = if (Test-Path $Config) { Get-Content $Config | ConvertFrom-Json } else { [pscustomobject]@{} }
if (-not $GameDir) { $GameDir = $saved.GameDir }
if (-not $GameDir) {
    $manifests = Join-Path $env:ProgramData 'Epic\EpicGamesLauncher\Data\Manifests'
    if (Test-Path $manifests) {
        $GameDir = Get-ChildItem $manifests -Filter *.item | ForEach-Object { Get-Content $_.FullName -Raw | ConvertFrom-Json } |
            Where-Object { $_.DisplayName -like '*Dauntless*' } | Select-Object -First 1 -ExpandProperty InstallLocation
    }
}
while (-not $GameDir -or -not (Test-Path (Join-Path $GameDir 'start_protected_game.exe'))) {
    $GameDir = (Read-Host 'Dauntless install folder (the one containing start_protected_game.exe)').Trim('"')
}
$exe = Join-Path $GameDir 'Archon\Binaries\Win64\Dauntless-Win64-Shipping.exe'
if (-not $saved.GameChecked) {
    Step 'Checking the game version'
    if ((Get-FileHash $exe -Algorithm SHA256).Hash -ne $Sha211) {
        Write-Host 'This is not the Dauntless 2.1.1 (CL 682875) client this server is built for; it will probably not load in.' -ForegroundColor Yellow
        if ((Read-Host 'Continue anyway? [y/N]') -notmatch '^[yY]') { exit 1 }
    }
}
if (-not $Name) { $Name = $saved.Name }
if (-not $Name) { $Name = Read-Host 'Player name (letters and digits, up to 16)'; if (-not $Name) { $Name = 'Player1' } }

# 4. Servers
function Up($port) { try { (Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false } }
if (-not (Up 3001)) {
    Step 'Starting the server'
    & (Join-Path $Root 'start-server.ps1')
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Up 3001) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    if (-not (Up 3001)) { throw "The backend did not start; see $Root\logs\backend.err.log" }
}

# 5. One-time: hosts entries + trusted certificate (administrator)
$hostsOk = (Get-Content "$env:SystemRoot\System32\drivers\etc\hosts" -Raw) -match 'phoenix-rising'
$caOk = Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -eq 'CN=Phoenix Rising Local CA' }
if (-not $hostsOk -or -not $caOk) {
    Step 'One-time setup: hosts entries and local certificate (administrator prompt)'
    Start-Process powershell -Verb RunAs -Wait -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $Root 'tools\setup-local.ps1'))
}

# 6. Remember the settings and play
@{ GameDir = $GameDir; Name = $Name; GameChecked = $true } | ConvertTo-Json | Set-Content $Config
Step "Launching Dauntless as '$Name'"
& (Join-Path $Root 'tools\launch-client.ps1') -GameDir $GameDir -Name $Name
Write-Host 'In the game window click Continue, then Defend the Frontier. Stop the server with Stop.bat.'
