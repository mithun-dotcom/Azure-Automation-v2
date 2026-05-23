"""
Admin-consent + OAuth for multi-tenant Graph access.

Flow:
  1. Customer Global Admin is sent to the Microsoft /adminconsent endpoint for OUR app.
  2. They approve the application permissions for THEIR tenant.
  3. Microsoft redirects back with their tenant id.
  4. From then on we mint app-only Graph tokens for that tenant via the client credentials
     flow (no user password ever involved).

Because we use *application* permissions with admin consent, the token is app-only and
scoped to exactly the permissions the admin approved. The admin can revoke consent at any
time from the Enterprise Applications blade, which instantly cuts our access.
"""
import msal
import httpx
from urllib.parse import urlencode
from .config import get_settings


def admin_consent_url(state: str) -> str:
    """URL to send a customer Global Admin to, to grant our app consent for their tenant."""
    s = get_settings()
    params = {
        "client_id": s.client_id,
        "redirect_uri": s.redirect_uri,
        "state": state,
        # 'common' lets any tenant's admin consent; their tenant id comes back on redirect.
        "scope": "https://graph.microsoft.com/.default",
    }
    return f"https://login.microsoftonline.com/common/adminconsent?{urlencode(params)}"


def acquire_app_token(tenant_id: str) -> str:
    """App-only Graph token for a consented tenant (client credentials flow)."""
    s = get_settings()
    app = msal.ConfidentialClientApplication(
        client_id=s.client_id,
        client_credential=s.client_secret,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    result = app.acquire_token_for_client(scopes=[s.graph_scope])
    if "access_token" not in result:
        raise RuntimeError(
            f"Token acquisition failed: {result.get('error')}: "
            f"{result.get('error_description')}"
        )
    return result["access_token"]


async def verify_tenant_consented(tenant_id: str) -> bool:
    """Confirm we can actually obtain a token + read the org (consent is live)."""
    try:
        token = acquire_app_token(tenant_id)
    except RuntimeError:
        return False
    s = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{s.graph_base}/organization",
            headers={"Authorization": f"Bearer {token}"},
        )
    return r.status_code == 200
