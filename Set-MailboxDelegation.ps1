<#
.SYNOPSIS
  Apply Full Access (and optionally Send-As) delegation across many mailboxes to one
  licensed user, using app-only certificate-based auth (no stored passwords).

.NOTES
  Invoked by the Python backend. Connects with the multi-tenant app's certificate.
  Outputs one JSON object per line so the backend can stream results.
#>
param(
  [Parameter(Mandatory=$true)][string]$AppId,
  [Parameter(Mandatory=$true)][string]$Organization,   # tenant's *.onmicrosoft.com
  [Parameter(Mandatory=$true)][string]$CertPath,        # .pfx
  [Parameter(Mandatory=$true)][string]$CertPassword,
  [Parameter(Mandatory=$true)][string]$DelegateUpn,     # the licensed user getting access
  [Parameter(Mandatory=$true)][string]$MailboxCsv,      # CSV with a 'Mailbox' column
  [switch]$IncludeSendAs
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

$rows = Import-Csv -Path $MailboxCsv
foreach ($row in $rows) {
  $mbx = $row.Mailbox
  if ([string]::IsNullOrWhiteSpace($mbx)) { continue }
  try {
    Add-MailboxPermission -Identity $mbx -User $DelegateUpn `
        -AccessRights FullAccess -InheritanceType All -AutoMapping $true `
        -ErrorAction Stop | Out-Null

    if ($IncludeSendAs) {
      Add-RecipientPermission -Identity $mbx -Trustee $DelegateUpn `
          -AccessRights SendAs -Confirm:$false -ErrorAction Stop | Out-Null
    }
    Emit @{ phase = "delegate"; mailbox = $mbx; ok = $true }
  }
  catch {
    Emit @{ phase = "delegate"; mailbox = $mbx; ok = $false; error = $_.Exception.Message }
  }
}

Disconnect-ExchangeOnline -Confirm:$false | Out-Null
