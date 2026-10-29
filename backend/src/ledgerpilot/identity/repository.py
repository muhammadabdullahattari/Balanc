"""Identity lookups always establish an explicit, transaction-local scope."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ledgerpilot.database import set_context
from ledgerpilot.identity.models import ActionToken, AuthSession, RefreshToken, User


def by_email(db: Session, email: str) -> User | None:
    db.execute(text("SELECT set_config('app.login_email', :email, true)"), {"email": email})
    return db.scalar(select(User).where(User.email == email))


def by_token(db: Session, token_hash: str, purpose: str) -> ActionToken | None:
    db.execute(text("SELECT set_config('app.token_hash', :h, true)"), {"h": token_hash})
    token = db.scalar(
        select(ActionToken).where(
            ActionToken.token_hash == token_hash, ActionToken.purpose == purpose
        )
    )
    if token:
        set_context(db, token.user_id)
        return db.scalar(
            select(ActionToken).where(ActionToken.token_hash == token_hash).with_for_update()
        )
    return None


def refresh_record(db: Session, token_hash: str) -> RefreshToken | None:
    db.execute(text("SELECT set_config('app.token_hash', :h, true)"), {"h": token_hash})
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if token:
        set_context(db, token.user_id)
        # Lock family before token, consistently with logout and authentication.
        db.scalar(select(AuthSession).where(AuthSession.id == token.session_id).with_for_update())
        return db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
    return None


def session_record(db: Session, user_id: UUID, session_id: UUID) -> AuthSession | None:
    set_context(db, user_id)
    return db.scalar(
        select(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.user_id == user_id)
        .with_for_update()
    )
