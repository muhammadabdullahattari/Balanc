"""Global identities and durable, revocable authentication state."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "app"}
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str]
    password_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    verified: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    suspended: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "app"}
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    password_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mfa: Mapped[bool] = mapped_column(default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = {"schema": "app"}
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("app.sessions.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionToken(Base):
    __tablename__ = "action_tokens"
    __table_args__ = {"schema": "app"}
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MFAFactor(Base):
    __tablename__ = "mfa_factors"
    __table_args__ = {"schema": "app"}
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), primary_key=True)
    encrypted_secret: Mapped[str]
    last_counter: Mapped[int] = mapped_column(default=-1)
    verified: Mapped[bool] = mapped_column(default=False)


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    __table_args__ = (UniqueConstraint("user_id", "code_hash"), {"schema": "app"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthRateBucket(Base):
    __tablename__ = "auth_rate_buckets"
    __table_args__ = {"schema": "app"}
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int]
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
