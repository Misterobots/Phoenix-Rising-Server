param()
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$log = Join-Path $root 'logs\certificate-repair.log'
Start-Transcript -Path $log -Force
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open PowerShell as administrator, then run this script again.'
    }
    Write-Host "Installing for Windows account: $($identity.Name)"
    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new((Join-Path $root 'Backend\data\tls\ca.pem'))
    $der = Join-Path $root 'Backend\data\tls\ca.cer'
    [IO.File]::WriteAllBytes($der, $certificate.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    Import-Certificate -FilePath $der -CertStoreLocation Cert:\CurrentUser\Root -ErrorAction Stop | Out-Null
    $found = Get-ChildItem Cert:\CurrentUser\Root | Where-Object Thumbprint -eq $certificate.Thumbprint
    if (-not $found) { throw 'Windows did not persist the certificate. Certificate repair has NOT succeeded.' }
    $response = Invoke-WebRequest 'https://api.epicgames.dev/' -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -ne 200) { throw 'The local HTTPS check did not return 200.' }
    Write-Host 'Certificate retained and HTTPS verified. You can now run Play.bat.' -ForegroundColor Green
} finally {
    Stop-Transcript
}
