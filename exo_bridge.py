"""
Bridge from Python to PowerShell 7 + ExchangeOnlineManagement for the two operations
Graph doesn't expose: mailbox Full Access delegation and the org-wide SMTP AUTH toggle.

NOTE ON DEPLOYMENT:
The two PowerShell scripts are EMBEDDED here as strings and written to disk on first use.
This deliberately avoids shipping a separate `powershell/` folder, because folder uploads
to GitHub's web uploader can silently drop subfolders (which broke the Docker build with
'"/powershell": not found'). With the scripts embedded, the only thing that must deploy is
the `backend/` package itself.
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from .config import get_settings

# Where we materialize the scripts at runtime (writable tmp dir).
_SCRIPT_DIR = Path(tempfile.gettempdir()) / "exo_scripts"

_DELEGATION_PS1 = r'''<#
  Apply Full Access (and optionally Send-As) delegation across many mailboxes to one
  licensed user, using app-only certificate-based auth (no stored passwords).
  Outputs one JSON object per line so the backend can stream results.
#>
param(
  [Parameter(Mandatory=$true)][string]$AppId,
  [Parameter(Mandatory=$true)][string]$Organization,
  [Parameter(Mandatory=$true)][string]$CertPath,
  [Parameter(Mandatory=$true)][string]$CertPassword,
  [Parameter(Mandatory=$true)][string]$DelegateUpn,
  [Parameter(Mandatory=$true)][string]$MailboxCsv,
  [switch]$IncludeSendAs
)
$ErrorActionPreference = "Stop"
function Emit($obj) { $obj | ConvertTo-Json -Compress }
try {
  $securePwd = ConvertTo-SecureString $CertPassword -AsPlainText -Force
  $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertPath, $securePwd)
  Import-Module ExchangeOnlineManagement -ErrorAction Stop
  Connect-ExchangeOnline -AppId $AppId -Organization $Organization -Certificate $cert -ShowBanner:$false | Out-Null
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
    Add-MailboxPermission -Identity $mbx -User $DelegateUpn -AccessRights FullAccess -InheritanceType All -AutoMapping $true -ErrorAction Stop | Out-Null
    if ($IncludeSendAs) {
      Add-RecipientPermission -Identity $mbx -Trustee $DelegateUpn -AccessRights SendAs -Confirm:$false -ErrorAction Stop | Out-Null
    }
    Emit @{ phase = "delegate"; mailbox = $mbx; ok = $true }
  }
  catch {
    Emit @{ phase = "delegate"; mailbox = $mbx; ok = $false; error = $_.Exception.Message }
  }
}
Disconnect-ExchangeOnline -Confirm:$false | Out-Null
'''

_SMTP_PS1 = r'''<#
  Turn OFF SMTP AUTH (legacy basic auth for SMTP submission) for the whole organization.
  Microsoft-recommended security hardening. App-only certificate-based auth.
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
  Connect-ExchangeOnline -AppId $AppId -Organization $Organization -Certificate $cert -ShowBanner:$false | Out-Null
}
catch {
  Emit @{ phase = "connect"; ok = $false; error = $_.Exception.Message }
  exit 1
}
try {
  Set-TransportConfig -SmtpClientAuthenticationDisabled $true -ErrorAction Stop
  $current = Get-TransportConfig | Select-Object -ExpandProperty SmtpClientAuthenticationDisabled
  $overrides = Get-CASMailbox -ResultSize Unlimited | Where-Object { $_.SmtpClientAuthenticationDisabled -eq $false } | Select-Object -ExpandProperty PrimarySmtpAddress
  Emit @{ phase = "smtp_auth"; ok = $true; orgDisabled = $current; perMailboxOverrides = @($overrides) }
}
catch {
  Emit @{ phase = "smtp_auth"; ok = $false; error = $_.Exception.Message }
}
Disconnect-ExchangeOnline -Confirm:$false | Out-Null
'''

_SCRIPTS = {
    "Set-MailboxDelegation.ps1": _DELEGATION_PS1,
    "Disable-SmtpAuth.ps1": _SMTP_PS1,
}


def _ensure_scripts() -> Path:
    """Write the embedded scripts to a tmp dir once, return that dir."""
    _SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in _SCRIPTS.items():
        path = _SCRIPT_DIR / name
        if not path.exists():
            path.write_text(body, encoding="utf-8")
    return _SCRIPT_DIR


async def _run_pwsh(script: str, args: list[str]) -> list[dict]:
    """Run a PowerShell script, parse one JSON object per output line."""
    script_dir = _ensure_scripts()
    cmd = ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script_dir / script), *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    results = []
    for line in stdout.decode().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            results.append({"raw": line})
    if proc.returncode != 0 and not results:
        results.append({"ok": False, "error": stderr.decode()[:2000] or "pwsh failed"})
    return results


async def set_mailbox_delegation(*, organization: str, delegate_upn: str,
                                 mailbox_csv_path: str,
                                 include_send_as: bool = False) -> list[dict]:
    s = get_settings()
    args = [
        "-AppId", s.client_id,
        "-Organization", organization,
        "-CertPath", s.exo_cert_path,
        "-CertPassword", s.exo_cert_password,
        "-DelegateUpn", delegate_upn,
        "-MailboxCsv", mailbox_csv_path,
    ]
    if include_send_as:
        args.append("-IncludeSendAs")
    return await _run_pwsh("Set-MailboxDelegation.ps1", args)


async def disable_smtp_auth(*, organization: str) -> list[dict]:
    s = get_settings()
    args = [
        "-AppId", s.client_id,
        "-Organization", organization,
        "-CertPath", s.exo_cert_path,
        "-CertPassword", s.exo_cert_password,
    ]
    return await _run_pwsh("Disable-SmtpAuth.ps1", args)
