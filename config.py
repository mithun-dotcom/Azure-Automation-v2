"""Configuration loaded from environment variables (set these in Render)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Entra multi-tenant app registration
    client_id: str = ""
    client_secret: str = ""                 # used for Graph
    # Multi-tenant apps authenticate against the /organizations or tenant-specific authority
    graph_scope: str = "https://graph.microsoft.com/.default"
    graph_base: str = "https://graph.microsoft.com/v1.0"

    # Exchange Online PowerShell app-only (certificate-based) auth
    exo_cert_path: str = "/secrets/exo.pfx"  # mounted secret file on Render
    exo_cert_password: str = ""

    # Where the customer admin is sent back after granting consent / signing in
    redirect_uri: str = "https://your-backend.onrender.com/auth/callback"
    frontend_url: str = "https://your-frontend.netlify.app"

    # App-level secrets
    token_encryption_key: str = ""           # Fernet key, 32 url-safe base64 bytes
    session_jwt_secret: str = ""             # signs the SPA session token

    database_url: str = "sqlite+aiosqlite:///./provisioner.db"

    # Safety: directory role display names that must NEVER be password-reset in bulk.
    protected_roles: tuple[str, ...] = (
        "Global Administrator",
        "Privileged Role Administrator",
        "Privileged Authentication Administrator",
        "Security Administrator",
        "Exchange Administrator",
        "User Administrator",
        "Helpdesk Administrator",
        "Authentication Administrator",
        "Company Administrator",  # legacy name for Global Administrator
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
