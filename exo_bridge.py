"""
Bridge from Python to PowerShell 7 + ExchangeOnlineManagement for the two operations
Graph doesn't expose: mailbox Full Access delegation and the org-wide SMTP AUTH toggle.

The container (see Dockerfile) ships pwsh and the EXO module preinstalled. We resolve the
tenant's *.onmicrosoft.com organization name from Graph before connecting.
"""
import asyncio
import json
import os
from pathlib import Path
from .config import get_settings

PS_DIR = Path(__file__).resolve().parent.parent / "powershell"


async def _run_pwsh(script: str, args: list[str]) -> list[dict]:
    """Run a PowerShell script, parse one JSON object per output line."""
    cmd = ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(PS_DIR / script), *args]
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
