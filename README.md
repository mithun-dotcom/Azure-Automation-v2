# M365 Provisioner — Backend (Render)

FastAPI backend for multi-tenant Microsoft 365 provisioning, using **admin-consent OAuth**
(no stored passwords, MFA-safe). Deploys to Render as a Docker container that also carries
PowerShell 7 + the ExchangeOnlineManagement module for the two Exchange-only operations.

> **Deploy note (why there's no `powershell/` folder):** the two PowerShell scripts are
> embedded inside `backend/exo_bridge.py` and written to disk at runtime. Earlier builds
> failed with `"/powershell": not found` because GitHub's web "upload files" can silently
> drop subfolders, so the `powershell/` directory never reached Render's build context.
> Embedding the scripts means the Docker build only needs `backend/` (which uploads fine),
> and the Exchange features still work identically.

## Layout

```
Dockerfile              Python 3.12 + PowerShell 7 + EXO module
backend/
  __init__.py           FastAPI app + all routes
  config.py             env-var settings
  auth.py               admin-consent + app-only Graph token acquisition (MSAL)
  graph.py              Graph operations: domains, users, licenses, reset, block/unblock
  exo_bridge.py         subprocess bridge to PowerShell
  operations.py         two-phase bulk reset + server-side admin-exclusion guard
  audit.py              append-only audit log (SQLAlchemy async)
  crypto.py             Fernet encryption helpers for tokens at rest
  requirements.txt
  exo_bridge.py         subprocess bridge to PowerShell — the two Exchange scripts are
                        EMBEDDED here and written to a temp dir at runtime (no separate
                        powershell/ folder to upload)
csv-templates/          users.csv, mailboxes.csv, password-reset.csv
```

## API routes

```
GET  /auth/consent-url                      URL to send a customer Global Admin to
GET  /auth/callback                         Microsoft redirects here post-consent
GET  /tenant/{tid}/skus                      available licenses
POST /tenant/{tid}/domains                   add domain
GET  /tenant/{tid}/domains/{d}/records       DNS records to publish
POST /tenant/{tid}/domains/{d}/verify        verify domain
POST /tenant/{tid}/users                     create one user (+ optional license)
POST /tenant/{tid}/users/bulk                bulk create from CSV
POST /tenant/{tid}/delegate                  Full Access delegation (EXO)
POST /tenant/{tid}/smtp-auth/disable         org-wide SMTP AUTH off (gated)
POST /tenant/{tid}/reset/preview             build reset plan, no changes made
POST /tenant/{tid}/reset/execute             execute reset (needs confirmation_token)
GET  /tenant/{tid}/audit                      recent audit entries
GET  /healthz                                 health check
```

## Run locally

```bash
cd backend
pip install -r requirements.txt
# PowerShell 7 + ExchangeOnlineManagement must be installed for the EXO routes:
#   pwsh -c "Install-Module ExchangeOnlineManagement -Scope CurrentUser -Force"
uvicorn backend:app --reload --port 8000
```

The EXO routes (`/delegate`, `/smtp-auth/disable`) only work where `pwsh` is on PATH.
Everything else (Graph routes) works without PowerShell.

## Deploy to Render

1. Push this folder to GitHub. Render → **New → Web Service**, environment **Docker**.
2. Mount your app certificate `.pfx` as a **Secret File** at `/secrets/exo.pfx`.
3. Environment variables:

   ```
   CLIENT_ID=<app client id>
   CLIENT_SECRET=<graph client secret>
   EXO_CERT_PATH=/secrets/exo.pfx
   EXO_CERT_PASSWORD=<pfx password>
   REDIRECT_URI=https://your-backend.onrender.com/auth/callback
   FRONTEND_URL=https://your-frontend.netlify.app
   TOKEN_ENCRYPTION_KEY=<see below>
   SESSION_JWT_SECRET=<random 32+ chars>
   ```

   Generate the encryption key:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
4. Deploy, then check `https://your-backend.onrender.com/healthz`.

## Entra app registration (one-time)

Register ONE multi-tenant app. Graph **application** permissions: `Domain.ReadWrite.All`,
`User.ReadWrite.All`, `Directory.ReadWrite.All`, `Organization.Read.All`,
`User.EnableDisableAccount.All`. Plus **Office 365 Exchange Online → `Exchange.ManageAsApp`**.
Assign an Entra directory role (e.g. Exchange Administrator) to the app's service principal
so EXO RBAC works. Create a **client secret** (Graph) and **upload a certificate** (EXO).
Each customer grants admin consent for their own tenant via `/auth/consent-url`.

## Safety behavior

- Bulk password reset is two-phase (preview → confirm) and **never** resets accounts
  holding a protected admin role — enforced server-side in `operations.py`, see
  `Settings.protected_roles` in `config.py`.
- SMTP AUTH disable and reset-execute both require explicit confirmation.
- Every change is written to the audit log.

The frontend (Netlify) is a separate bundle — point it at this backend via `VITE_API_URL`.
