"""
Microsoft Graph operations for the provisioning workflow.

All calls use an app-only token for a consented tenant. Each method maps to a
documented Graph endpoint. Destructive bulk operations are NOT here — they live behind
the gated service layer in operations.py so they can't be triggered without confirmation.
"""
import httpx
from .config import get_settings
from .auth import acquire_app_token


class GraphClient:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.base = get_settings().graph_base

    async def _headers(self) -> dict:
        token = acquire_app_token(self.tenant_id)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # ---- Domains -------------------------------------------------------------
    async def add_domain(self, domain: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/domains",
                             headers=await self._headers(),
                             json={"id": domain})
            r.raise_for_status()
            return r.json()

    async def get_domain_verification_records(self, domain: str) -> dict:
        """Returns the DNS records the customer must publish (TXT/MX) to prove ownership."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.base}/domains/{domain}/verificationDnsRecords",
                headers=await self._headers())
            r.raise_for_status()
            return r.json()

    async def verify_domain(self, domain: str) -> dict:
        """Ask Microsoft to check the DNS records and mark the domain verified."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/domains/{domain}/verify",
                             headers=await self._headers())
            r.raise_for_status()
            return r.json()

    # ---- Licenses ------------------------------------------------------------
    async def list_available_skus(self) -> list[dict]:
        """Subscribed SKUs with remaining seats (enabled - consumed)."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.base}/subscribedSkus", headers=await self._headers())
            r.raise_for_status()
        out = []
        for sku in r.json().get("value", []):
            enabled = sku["prepaidUnits"]["enabled"]
            consumed = sku["consumedUnits"]
            out.append({
                "skuId": sku["skuId"],
                "skuPartNumber": sku["skuPartNumber"],
                "available": enabled - consumed,
            })
        return out

    async def assign_license(self, user_id: str, sku_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{self.base}/users/{user_id}/assignLicense",
                headers=await self._headers(),
                json={"addLicenses": [{"skuId": sku_id, "disabledPlans": []}],
                      "removeLicenses": []})
            r.raise_for_status()
            return r.json()

    # ---- Users ---------------------------------------------------------------
    async def create_user(self, *, display_name: str, upn: str,
                          password: str, force_change: bool = True) -> dict:
        body = {
            "accountEnabled": True,
            "displayName": display_name,
            "userPrincipalName": upn,
            "mailNickname": upn.split("@")[0],
            "passwordProfile": {
                "forceChangePasswordNextSignIn": force_change,
                "password": password,
            },
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/users",
                             headers=await self._headers(), json=body)
            r.raise_for_status()
            return r.json()

    async def list_users(self) -> list[dict]:
        users, url = [], f"{self.base}/users?$select=id,displayName,userPrincipalName,accountEnabled&$top=999"
        async with httpx.AsyncClient(timeout=60) as c:
            while url:
                r = await c.get(url, headers=await self._headers())
                r.raise_for_status()
                data = r.json()
                users.extend(data.get("value", []))
                url = data.get("@odata.nextLink")
        return users

    async def get_user_directory_roles(self, user_id: str) -> list[str]:
        """Role display names this user holds — used by the admin-exclusion guard."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.base}/users/{user_id}/transitiveMemberOf/microsoft.graph.directoryRole?$select=displayName",
                headers=await self._headers())
            if r.status_code != 200:
                return []
            return [x.get("displayName", "") for x in r.json().get("value", [])]

    async def reset_password(self, user_id: str, new_password: str,
                             force_change: bool = True) -> None:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.patch(
                f"{self.base}/users/{user_id}",
                headers=await self._headers(),
                json={"passwordProfile": {
                    "forceChangePasswordNextSignIn": force_change,
                    "password": new_password}})
            r.raise_for_status()

    async def set_account_enabled(self, user_id: str, enabled: bool) -> None:
        """Unblock (enabled=True) or block (False) sign-in."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.patch(f"{self.base}/users/{user_id}",
                              headers=await self._headers(),
                              json={"accountEnabled": enabled})
            r.raise_for_status()
