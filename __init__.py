"""
FastAPI surface for the Netlify frontend.

Routes:
  GET  /auth/consent-url        -> URL to send a customer Global Admin to grant consent
  GET  /auth/callback           -> Microsoft redirects here after admin consent
  GET  /tenant/{tid}/skus       -> available licenses
  POST /tenant/{tid}/domains    -> add domain
  GET  /tenant/{tid}/domains/{d}/records  -> DNS records to publish
  POST /tenant/{tid}/domains/{d}/verify   -> verify domain
  POST /tenant/{tid}/users      -> create one user (+ optional license)
  POST /tenant/{tid}/users/bulk -> create many users from CSV
  POST /tenant/{tid}/delegate   -> Full Access delegation across mailboxes (EXO)
  POST /tenant/{tid}/smtp-auth/disable  -> org-wide SMTP AUTH off (EXO, gated)
  POST /tenant/{tid}/reset/preview      -> build reset plan (no changes made)
  POST /tenant/{tid}/reset/execute      -> execute reset (requires confirmation_token)
  GET  /tenant/{tid}/audit      -> recent audit entries
"""
import secrets
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .config import get_settings
from .auth import admin_consent_url, verify_tenant_consented
from .graph import GraphClient
from .exo_bridge import set_mailbox_delegation, disable_smtp_auth
from . import operations as ops
from .audit import Audit, init_db

app = FastAPI(title="M365 Tenant Provisioner")
audit = Audit()
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_methods=["*"], allow_headers=["*"], allow_credentials=True,
)

# In-memory cache of reset plans keyed by confirmation_token (short-lived).
_reset_plans: dict[str, tuple[str, ops.ResetPlan, bytes]] = {}


@app.on_event("startup")
async def _startup():
    await init_db()


# ---- Auth / consent ---------------------------------------------------------
@app.get("/auth/consent-url")
async def consent_url():
    state = secrets.token_urlsafe(16)
    return {"url": admin_consent_url(state), "state": state}


@app.get("/auth/callback")
async def callback(tenant: str | None = Query(default=None),
                   admin_consent: bool | None = Query(default=None),
                   error: str | None = Query(default=None),
                   error_description: str | None = Query(default=None)):
    if error:
        return RedirectResponse(
            f"{settings.frontend_url}/?consent=error&reason={error}")
    if tenant and await verify_tenant_consented(tenant):
        await audit.record(tenant, "consent_granted", tenant, "ok")
        return RedirectResponse(f"{settings.frontend_url}/?consent=ok&tenant={tenant}")
    return RedirectResponse(f"{settings.frontend_url}/?consent=pending")


async def _org_onmicrosoft(tenant_id: str) -> str:
    """Resolve the tenant's *.onmicrosoft.com name (needed by EXO connect)."""
    graph = GraphClient(tenant_id)
    async with __import__("httpx").AsyncClient(timeout=30) as c:
        r = await c.get(f"{settings.graph_base}/organization?$select=verifiedDomains",
                        headers=await graph._headers())
        r.raise_for_status()
    for org in r.json().get("value", []):
        for d in org.get("verifiedDomains", []):
            if d.get("isInitial"):
                return d["name"]
    raise HTTPException(500, "Could not resolve onmicrosoft.com domain")


# ---- Licenses & domains -----------------------------------------------------
@app.get("/tenant/{tid}/skus")
async def skus(tid: str):
    return await GraphClient(tid).list_available_skus()


class DomainBody(BaseModel):
    domain: str


@app.post("/tenant/{tid}/domains")
async def add_domain(tid: str, body: DomainBody):
    res = await GraphClient(tid).add_domain(body.domain)
    await audit.record(tid, "add_domain", body.domain, "ok")
    return res


@app.get("/tenant/{tid}/domains/{domain}/records")
async def domain_records(tid: str, domain: str):
    return await GraphClient(tid).get_domain_verification_records(domain)


@app.post("/tenant/{tid}/domains/{domain}/verify")
async def verify_domain(tid: str, domain: str):
    res = await GraphClient(tid).verify_domain(domain)
    await audit.record(tid, "verify_domain", domain, "ok")
    return res


# ---- Users ------------------------------------------------------------------
class UserBody(BaseModel):
    display_name: str
    upn: str
    password: str
    sku_id: str | None = None


@app.post("/tenant/{tid}/users")
async def create_user(tid: str, body: UserBody):
    graph = GraphClient(tid)
    user = await graph.create_user(display_name=body.display_name,
                                   upn=body.upn, password=body.password)
    if body.sku_id:
        await graph.assign_license(user["id"], body.sku_id)
    await audit.record(tid, "create_user", body.upn, "ok")
    return user


@app.post("/tenant/{tid}/users/bulk")
async def bulk_users(tid: str, sku_id: str | None = Form(default=None),
                     file: UploadFile = File(...)):
    """CSV columns: DisplayName, UserPrincipalName, Password."""
    import csv as _csv, io as _io
    graph = GraphClient(tid)
    content = (await file.read()).decode("utf-8-sig")
    results = []
    for row in _csv.DictReader(_io.StringIO(content)):
        upn = (row.get("UserPrincipalName") or "").strip()
        try:
            user = await graph.create_user(
                display_name=(row.get("DisplayName") or upn).strip(),
                upn=upn, password=(row.get("Password") or "").strip())
            if sku_id:
                await graph.assign_license(user["id"], sku_id)
            await audit.record(tid, "create_user", upn, "ok")
            results.append({"upn": upn, "ok": True})
        except Exception as e:  # noqa: BLE001
            await audit.record(tid, "create_user", upn, f"error: {e}")
            results.append({"upn": upn, "ok": False, "error": str(e)})
    return {"results": results}


# ---- Exchange: delegation + SMTP AUTH (gated) -------------------------------
@app.post("/tenant/{tid}/delegate")
async def delegate(tid: str, delegate_upn: str = Form(...),
                   include_send_as: bool = Form(default=False),
                   file: UploadFile = File(...)):
    """CSV with a 'Mailbox' column. Grants the licensed user Full Access to each."""
    import tempfile, os
    org = await _org_onmicrosoft(tid)
    content = await file.read()
    with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        res = await set_mailbox_delegation(
            organization=org, delegate_upn=delegate_upn,
            mailbox_csv_path=path, include_send_as=include_send_as)
    finally:
        os.unlink(path)
    ok = sum(1 for r in res if r.get("ok"))
    await audit.record(tid, "mailbox_delegation",
                       f"{delegate_upn} ({ok} mailboxes)", "ok")
    return {"results": res}


class ConfirmBody(BaseModel):
    confirm: bool


@app.post("/tenant/{tid}/smtp-auth/disable")
async def smtp_auth_disable(tid: str, body: ConfirmBody):
    """Org-wide SMTP AUTH off. Requires explicit confirm=true (UI shows a warning gate)."""
    if not body.confirm:
        raise HTTPException(400, "Confirmation required: set confirm=true")
    org = await _org_onmicrosoft(tid)
    res = await disable_smtp_auth(organization=org)
    await audit.record(tid, "disable_smtp_auth", org, "ok")
    return {"results": res}


# ---- Bulk password reset (two-phase, admin-excluded) ------------------------
@app.post("/tenant/{tid}/reset/preview")
async def reset_preview(tid: str, file: UploadFile = File(...)):
    content = await file.read()
    plan = await ops.build_reset_plan(tid, content)
    _reset_plans[plan.confirmation_token] = (tid, plan, content)
    return {
        "confirmation_token": plan.confirmation_token,
        "will_reset": plan.will_reset,
        "excluded_admins": plan.excluded_admins,   # surfaced so the operator sees the guard
        "not_found": plan.not_found,
        "summary": {
            "to_reset": len(plan.will_reset),
            "excluded": len(plan.excluded_admins),
            "not_found": len(plan.not_found),
        },
    }


class ExecuteResetBody(BaseModel):
    confirmation_token: str
    unblock_signin: bool = True


@app.post("/tenant/{tid}/reset/execute")
async def reset_execute(tid: str, body: ExecuteResetBody):
    cached = _reset_plans.get(body.confirmation_token)
    if not cached or cached[0] != tid:
        raise HTTPException(400, "Invalid or expired confirmation token. Re-run preview.")
    _tid, plan, content = cached
    results = await ops.execute_reset(tid, content, plan, body.unblock_signin, audit)
    del _reset_plans[body.confirmation_token]
    return {"results": results}


@app.get("/tenant/{tid}/audit")
async def get_audit(tid: str):
    return await audit.recent(tid)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
