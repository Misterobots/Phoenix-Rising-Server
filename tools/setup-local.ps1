# One-time local setup for the Phoenix Rising server (run from an elevated PowerShell):
#   1. hosts entries that send the client's service hostnames to this machine (the backend answers on 443),
#   2. trust the backend's local CA (Backend\data\tls\ca.pem, created on the backend's first start) for this user.
#
#   .\tools\setup-local.ps1            add both
#   .\tools\setup-local.ps1 -Remove    remove the hosts entries and the CA

param([switch]$Remove)

$Hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
$Names = 'api.epicgames.dev', 'connect.epicgames.dev', 'account-public-service-prod03.ol.epicgames.com',
         'egp-idsoc-proxy-prod.ol.epicgames.com', 'xmpp-service-prod.ol.epicgames.com',
         'tracking-prod.steelyard.ca', 'telemetry-ingest-prod.steelyard.ca'
$Tag = '# phoenix-rising'
$Ca = Join-Path $PSScriptRoot '..\Backend\data\tls\ca.pem'

$kept = Get-Content $Hosts | Where-Object { $_ -notmatch [regex]::Escape($Tag) }
if ($Remove) {
    Set-Content $Hosts $kept
    Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -eq 'CN=Phoenix Rising Local CA' } | Remove-Item
    Write-Host 'hosts entries and local CA removed'
    return
}
Set-Content $Hosts ($kept + ($Names | ForEach-Object { "127.0.0.1 $_ $Tag" }))
Write-Host "hosts: $($Names.Count) entries -> 127.0.0.1"
if (-not (Test-Path $Ca)) { Write-Warning "no $Ca yet: start the server once (.\start-server.ps1), then run this again"; return }
Import-Certificate -FilePath $Ca -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
Write-Host 'local CA trusted for the current user'
