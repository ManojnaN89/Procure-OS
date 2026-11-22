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
    # setup, cutover, live or suspended (company_state.py is the only writer)
    state: Mapped[str] = mapped_column(String(16), server_default="setup")
    state_before_suspension: Mapped[str | None] = mapped_column(String(16))
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantStateChange(ControlBase):
    """One move of a company between states; insert only to the app."""

    __tablename__ = "tenant_state_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"))
    from_state: Mapped[str] = mapped_column(Text)
    to_state: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    by_kind: Mapped[str] = mapped_column(Text)  # user, operator or system
    by_id: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(Text)  # a suspension: commercial or security
    reason: Mapped[str] = mapped_column(Text)


class TenantArchive(ControlBase):
    """The setup database a cutover kept: read only by the platform team as the owner."""

    __tablename__ = "tenant_archive"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"))
    database_name: Mapped[str] = mapped_column(Text, unique=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    operator: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    audit_rows: Mapped[int]
    audit_last_hash: Mapped[str | None] = mapped_column(Text)
    setup_change_last_hash: Mapped[str | None] = mapped_column(Text)


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


class AppUser(ControlBase):
    """A person who can log in. Email is stored in lower case and is unique across all tenants."""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    failed_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # set once the profile is written in the company's database: until then the login cannot sign in (DF-36)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(ControlBase):
    """Only the hash of the token is stored."""

    __tablename__ = "refresh_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
