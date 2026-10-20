# One-time local setup for the Phoenix Rising server (run from an elevated PowerShell):
#   1. hosts entries that send the client's service hostnames to this machine (the backend answers on 443),
#   2. trust the backend's local CA (Backend\data\tls\ca.pem, created on the backend's first start).
#
#   .\tools\setup-local.ps1            add both
#   .\tools\setup-local.ps1 -Remove    remove the hosts entries and the CA

param([switch]$Remove)
$ErrorActionPreference = 'Stop'

$Hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
$Names = 'api.epicgames.dev', 'connect.epicgames.dev', 'account-public-service-prod03.ol.epicgames.com',
         'egp-idsoc-proxy-prod.ol.epicgames.com', 'xmpp-service-prod.ol.epicgames.com',
         'tracking-prod.steelyard.ca', 'telemetry-ingest-prod.steelyard.ca'
$Tag = '# phoenix-rising'
$Root = Split-Path $PSScriptRoot -Parent
$Ca = Join-Path $Root 'Backend\data\tls\ca.pem'

$kept = Get-Content $Hosts | Where-Object { $_ -notmatch [regex]::Escape($Tag) }
if ($Remove) {
    Set-Content $Hosts $kept
    Get-ChildItem Cert:\LocalMachine\Root,Cert:\CurrentUser\Root -ErrorAction SilentlyContinue | Where-Object { $_.Subject -eq 'CN=Phoenix Rising Local CA' } | Remove-Item -ErrorAction SilentlyContinue
    Write-Host 'hosts entries and local CA removed'
    return
}
Set-Content $Hosts ($kept + ($Names | ForEach-Object { "127.0.0.1 $_ $Tag" }))
Write-Host "hosts: $($Names.Count) entries -> 127.0.0.1"
if (-not (Test-Path $Ca)) { Write-Warning "no $Ca yet: start the server once (.\start-server.ps1), then run this again"; return }
$certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($Ca)
$derPath = [IO.Path]::ChangeExtension($Ca, '.cer')
[IO.File]::WriteAllBytes($derPath, $certificate.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
Import-Certificate -FilePath $derPath -CertStoreLocation Cert:\LocalMachine\Root -ErrorAction Stop | Out-Null
if (-not (Get-ChildItem Cert:\LocalMachine\Root,Cert:\CurrentUser\Root -ErrorAction SilentlyContinue | Where-Object { $_.Thumbprint -eq $certificate.Thumbprint })) {
    throw 'Windows did not retain the Phoenix Rising CA in the trusted root store.'
}
Write-Host 'local CA trusted'
