"""
Service layer for the bulk / destructive operations. This is where the guardrails live:

  * Bulk password reset and bulk sign-in changes require a two-phase confirmation:
      1) /preview returns a plan (who would be affected, who is excluded and why)
      2) /execute requires the exact confirmation_token returned by the preview
  * The admin-exclusion guard removes any account holding a protected directory role
    BEFORE execution, server-side. The UI cannot override this.
  * Every executed change is written to the audit log.

These controls protect the operator and make customer consent reviews straightforward.
"""
import csv
import io
import secrets
from dataclasses import dataclass, field
from .config import get_settings
from .graph import GraphClient


@dataclass
class ResetPlan:
    confirmation_token: str
    will_reset: list[dict] = field(default_factory=list)      # {upn, user_id}
    excluded_admins: list[dict] = field(default_factory=list) # {upn, roles}
    not_found: list[str] = field(default_factory=list)


def parse_reset_csv(content: bytes) -> dict[str, str]:
    """CSV with columns: UserPrincipalName, NewPassword -> {upn_lower: password}."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    out = {}
    for row in reader:
        upn = (row.get("UserPrincipalName") or row.get("UPN") or "").strip()
        pwd = (row.get("NewPassword") or row.get("Password") or "").strip()
        if upn and pwd:
            out[upn.lower()] = pwd
    return out


async def build_reset_plan(tenant_id: str, csv_content: bytes) -> ResetPlan:
    """
    Match CSV rows to real users, then split into 'will reset' vs 'excluded admin'.
    The admin-exclusion guard is enforced HERE, not in the UI.
    """
    settings = get_settings()
    protected = {r.lower() for r in settings.protected_roles}
    graph = GraphClient(tenant_id)

    requested = parse_reset_csv(csv_content)
    all_users = await graph.list_users()
    by_upn = {u["userPrincipalName"].lower(): u for u in all_users}

    plan = ResetPlan(confirmation_token=secrets.token_urlsafe(24))
    for upn_lower in requested:
        user = by_upn.get(upn_lower)
        if not user:
            plan.not_found.append(upn_lower)
            continue
        roles = await graph.get_user_directory_roles(user["id"])
        held_protected = [r for r in roles if r.lower() in protected]
        if held_protected:
            plan.excluded_admins.append({"upn": user["userPrincipalName"],
                                         "roles": held_protected})
        else:
            plan.will_reset.append({"upn": user["userPrincipalName"],
                                    "user_id": user["id"]})
    return plan


async def execute_reset(tenant_id: str, csv_content: bytes,
                        plan: ResetPlan, unblock_signin: bool,
                        audit) -> list[dict]:
    """Execute only the non-admin resets from a previously-built plan."""
    graph = GraphClient(tenant_id)
    requested = parse_reset_csv(csv_content)
    results = []
    for target in plan.will_reset:
        upn = target["upn"]
        new_pwd = requested.get(upn.lower())
        if not new_pwd:
            results.append({"upn": upn, "ok": False, "error": "password missing from CSV"})
            continue
        try:
            await graph.reset_password(target["user_id"], new_pwd, force_change=True)
            if unblock_signin:
                await graph.set_account_enabled(target["user_id"], True)
            await audit.record(tenant_id, "password_reset", upn, "ok")
            results.append({"upn": upn, "ok": True, "unblocked": unblock_signin})
        except Exception as e:  # noqa: BLE001 - report per-row, keep going
            await audit.record(tenant_id, "password_reset", upn, f"error: {e}")
            results.append({"upn": upn, "ok": False, "error": str(e)})
    return results
