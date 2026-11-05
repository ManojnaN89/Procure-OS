"""Control database: which tenants exist, where their databases are, flags and settings."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ControlBase(DeclarativeBase):
    pass


class Tenant(ControlBase):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantDatabase(ControlBase):
    __tablename__ = "tenant_database"

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    database_name: Mapped[str] = mapped_column(String(63), unique=True)
    # Fernet-encrypted SQLAlchemy URL; the key comes from TENANT_SECRET_KEY
    connection_encrypted: Mapped[str] = mapped_column(Text)
    schema_revision: Mapped[str | None] = mapped_column(String(64))
    migrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeatureFlag(ControlBase):
    __tablename__ = "feature_flag"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"))
    key: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class TenantSetting(ControlBase):
    __tablename__ = "tenant_setting"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[object] = mapped_column(JSON)
