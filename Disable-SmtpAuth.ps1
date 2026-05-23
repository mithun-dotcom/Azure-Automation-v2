<#
.SYNOPSIS
  Turn OFF SMTP AUTH (legacy basic auth for SMTP submission) for the whole organization.
  This is a Microsoft-recommended security hardening step; basic auth is being retired.

.NOTES
  App-only certificate-based auth. Sets SmtpClientAuthenticationDisabled = $true on the
  organization transport config. Per-mailbox overrides may still exist and are reported.
#>
param(
  [Parameter(Mandatory=$true)][string]$AppId,
  [Parameter(Mandatory=$true)][string]$Organization,
  [Parameter(Mandatory=$true)][string]$CertPath,
  [Parameter(Mandatory=$true)][string]$CertPassword
)

$ErrorActionPreference = "Stop"
function Emit($obj) { $obj | ConvertTo-Json -Compress }

try {
  $securePwd = ConvertTo-SecureString $CertPassword -AsPlainText -Force
  $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertPath, $securePwd)
  Import-Module ExchangeOnlineManagement -ErrorAction Stop
  Connect-ExchangeOnline -AppId $AppId -Organization $Organization `
      -Certificate $cert -ShowBanner:$false | Out-Null
}
catch {
  Emit @{ phase = "connect"; ok = $false; error = $_.Exception.Message }
  exit 1
}

try {
  Set-TransportConfig -SmtpClientAuthenticationDisabled $true -ErrorAction Stop
  $current = Get-TransportConfig | Select-Object -ExpandProperty SmtpClientAuthenticationDisabled

  # Report any mailboxes that override the org setting (would still allow SMTP AUTH).
  $overrides = Get-CASMailbox -ResultSize Unlimited |
      Where-Object { $_.SmtpClientAuthenticationDisabled -eq $false } |
      Select-Object -ExpandProperty PrimarySmtpAddress

  Emit @{ phase = "smtp_auth"; ok = $true; orgDisabled = $current; perMailboxOverrides = @($overrides) }
}
catch {
  Emit @{ phase = "smtp_auth"; ok = $false; error = $_.Exception.Message }
}

Disconnect-ExchangeOnline -Confirm:$false | Out-Null
