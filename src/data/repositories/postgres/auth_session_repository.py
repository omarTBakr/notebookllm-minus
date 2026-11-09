from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from data.models import AuthSession
from shared.exceptions import DbError

from ..interfaces.auth_session_repository import AuthSessionRepository
from .base_repository import AuthSessionRow, PostgresBaseRepository


class PostgresAuthSessionRepository(PostgresBaseRepository, AuthSessionRepository):
    """Signed-in browsers, by the sha256 of their cookie token."""

    async def create(self, session: AuthSession) -> None:
        try:
            async with self.session_factory.begin() as db:
                db.add(
                    AuthSessionRow(
                        id=self._generate_id(),
                        token_hash=session.token_hash,
                        user_id=session.user_id,
                        created_at=session.created_at,
                        expires_at=session.expires_at,
                        last_seen_at=session.last_seen_at,
                    )
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create auth session: {exc}") from exc

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        try:
            async with self.session_factory() as db:
                row = await db.scalar(select(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash))
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to read auth session: {exc}") from exc
        return self._record_to_model(row, AuthSession) if row is not None else None

    async def touch(self, token_hash: str, last_seen_at: datetime, expires_at: datetime) -> None:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    update(AuthSessionRow)
                    .where(AuthSessionRow.token_hash == token_hash)
                    .values(last_seen_at=last_seen_at, expires_at=expires_at)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to refresh auth session: {exc}") from exc

    async def delete(self, token_hash: str) -> bool:
        return await self._delete(AuthSessionRow.token_hash == token_hash) > 0

    async def delete_for_user(self, user_id: str, keep_token_hash: str | None = None) -> int:
        condition = AuthSessionRow.user_id == user_id
        if keep_token_hash:
            condition = condition & (AuthSessionRow.token_hash != keep_token_hash)
        return await self._delete(condition)

    async def delete_expired(self, now: datetime) -> int:
        return await self._delete(AuthSessionRow.expires_at <= now)

    async def _delete(self, condition) -> int:
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(delete(AuthSessionRow).where(condition))
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete auth sessions: {exc}") from exc
        return result.rowcount
