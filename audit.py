"""Append-only audit log of every change the tool makes to a tenant."""
import datetime as dt
from sqlalchemy import String, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .config import get_settings


class Base(DeclarativeBase):
    pass


class AuditEntry(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.now(dt.UTC))
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(256))
    result: Mapped[str] = mapped_column(String(512))


class TenantConsent(Base):
    """Record that a tenant's admin granted consent (no tokens stored here)."""
    __tablename__ = "tenant_consent"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    consented_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC))
    onmicrosoft_domain: Mapped[str] = mapped_column(String(256), default="")


_engine = create_async_engine(get_settings().database_url, echo=False)
_Session = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


class Audit:
    async def record(self, tenant_id: str, action: str, target: str, result: str) -> None:
        async with _Session() as s:
            s.add(AuditEntry(tenant_id=tenant_id, action=action,
                             target=target, result=result))
            await s.commit()

    async def recent(self, tenant_id: str, limit: int = 200) -> list[dict]:
        from sqlalchemy import select
        async with _Session() as s:
            rows = (await s.execute(
                select(AuditEntry).where(AuditEntry.tenant_id == tenant_id)
                .order_by(AuditEntry.ts.desc()).limit(limit))).scalars().all()
        return [{"ts": r.ts.isoformat(), "action": r.action,
                 "target": r.target, "result": r.result} for r in rows]
