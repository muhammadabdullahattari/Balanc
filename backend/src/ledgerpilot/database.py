"""Bounded application/counter pools; transaction-local identity cannot leak on reuse."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import UserDefinedType

from ledgerpilot.config import Settings


class Base(DeclarativeBase):
    pass


class TransactionId(UserDefinedType[str]):
    """PostgreSQL xid8 is retained in metadata so schema tooling cannot drop it."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "XID8"


def build_engine(settings: Settings, *, rate_limits: bool = False) -> Engine:
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.rate_db_pool_size if rate_limits else settings.db_pool_size,
        max_overflow=0 if rate_limits else settings.db_max_overflow,
        pool_timeout=1 if rate_limits else settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "options": (
                f"-c statement_timeout={1500 if rate_limits else settings.db_statement_timeout_ms} "
                f"-c lock_timeout={500 if rate_limits else 3000} "
                f"-c idle_in_transaction_session_timeout={settings.db_idle_transaction_timeout_ms}"
            ),
            "prepare_threshold": None,
        },
    )

    @event.listens_for(engine, "connect")
    def validate_identity(connection: Any, record: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user=%s AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                    OR r.rolreplication OR r.rolbypassrls OR r.rolinherit)
                 AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid)
                 AND NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='app' AND nspowner=r.oid)
                 AND NOT EXISTS(SELECT 1 FROM pg_database
                    WHERE datname=current_database() AND datdba=r.oid)
                 AND NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='app' AND c.relowner=r.oid AND c.relkind IN ('r','p'))
                FROM pg_roles r WHERE r.rolname=current_user
            """,
                (settings.app_db_user,),
            )
            result = cursor.fetchone()
        connection.rollback()
        if not result or not result[0]:
            raise DisconnectionError("Runtime database identity violates least privilege")

    return engine


def set_context(session: Session, user_id: UUID, business_id: UUID | None = None) -> None:
    session.execute(
        text("SELECT set_config('app.user_id', :u, true), set_config('app.business_id', :b, true)"),
        {"u": str(user_id), "b": str(business_id) if business_id else ""},
    )


def transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory.begin() as session:
        yield session
