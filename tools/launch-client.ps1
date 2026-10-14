# Launch the stock, unmodified Dauntless 2.1.1 client against the local Phoenix Rising server.
# The game starts through its own Easy Anti-Cheat bootstrapper (start_protected_game.exe); nothing is injected or
# patched. All redirection to the local services is done with launch arguments.
#
#   .\tools\launch-client.ps1 -GameDir "D:\Games\Dauntless" [-Name Player1] [-Server 127.0.0.1:8790]

param(
    [Parameter(Mandatory = $true)][string]$GameDir,
    [string]$Name = 'Player1',
    [string]$Server = '127.0.0.1:8790',
    [string]$Backend = 'http://127.0.0.1:3001',
    [string]$Launcher = 'http://127.0.0.1:3000'
)

$Boot = Join-Path $GameDir 'start_protected_game.exe'
if (-not (Test-Path $Boot)) { throw "start_protected_game.exe not found in $GameDir" }

# One-time exchange code for a local profile (reused on later launches with the same name).
$body = @{ displayName = $Name; reuse = $true } | ConvertTo-Json
$code = (Invoke-RestMethod -Method Post -Uri "$Launcher/launcher/v1/local-profiles" -Body $body -ContentType 'application/json').exchangeCode

$phoenix = '-ini:Game:[OnlineSubsystemPhoenix]'
$clientArgs = @(
    '-EpicPortal', '-AUTH_TYPE=exchangecode', '-AUTH_LOGIN=unused', "-AUTH_PASSWORD=$code",
    '-epicapp=Jackal', '-epicenv=Prod', '-epicsandboxid=jackal', '-epicdeploymentid=53565ba467df4edbb6f5a3d939a8b4f2',
    '-ini:Engine:[EOSSDK.Platform.Jackal]:bDisableOverlay=true', '-ini:Engine:[EOSSDK.Platform.Jackal]:bDisableSocialOverlay=true',
    "${phoenix}:AuthEndpoint=$Backend/game/login",
    "${phoenix}:AuthAvailableEndpoint=$Backend/checkavailable",
    "${phoenix}:AccountInfoEndpoint=$Backend/accountinfo",
    "${phoenix}:DauntlessSessionTokenEndpoint=$Backend/gamesession/{linkedaccountservice}",
    "${phoenix}:QueryLoginQueueEndpoint=$Backend/login",
    "${phoenix}:PhoenixStatusMessageEndpoint=$Backend/dauntless-status",
    '-ini:Engine:[OnlineSubsystemMcp.XMPP]:bUseSSL=false', '-ini:Engine:[OnlineSubsystemMcp.XMPP]:ServerAddr=ws://127.0.0.1',
    '-ini:Engine:[OnlineSubsystemMcp.XMPP]:ServerPort=3000', '-ini:Engine:[OnlineSubsystemMcp]:bEnabled=true',
    '-ini:Engine:[OnlineSubsystemMcp]:bEnableXmpp=true', '-ini:Engine:[OnlineSubsystemMcp.OnlineIdentityMcp]:bAutoLoginToXmpp=true',
    '-ini:Engine:[/Script/Engine.NetworkSettings]:n.VerifyPeer=false',
    '-log', "$Server/ramsgate_01_persistent"
)
Start-Process -FilePath $Boot -ArgumentList $clientArgs -WorkingDirectory $GameDir
Write-Host "Launched Dauntless as '$Name'. In the game window click Continue, then Defend the Frontier."
