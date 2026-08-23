from typing import AsyncIterator

from exceptions import SessionNotFoundError, DbError
from models.db_schema import Session
from .base_repository import PostgresBaseRepository
from ..interfaces.session_repository import SessionRepository


class PostgresSessionRepository(PostgresBaseRepository, SessionRepository):
    """PostgreSQL implementation of SessionRepository."""

    async def create_session(self, session: Session) -> str:
        record_id = self._generate_id()
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO sessions (id, session_id, user_id, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (session_id) DO NOTHING
                    """,
                    record_id,
                    session.session_id,
                    session.user_id,
                    session.created_at,
                    session.updated_at,
                )
            return session.session_id
        except Exception as exc:
            raise DbError(f"Failed to create session: {exc}") from exc

    async def get_session(self, session_id: str) -> Session:
        try:
            async with self.pool.acquire() as conn:
                record = await conn.fetchrow(
                    "SELECT * FROM sessions WHERE session_id = $1", session_id
                )
        except Exception as exc:
            raise DbError(f"Failed to get session: {exc}") from exc

        if not record:
            raise SessionNotFoundError(f"Session {session_id!r} not found")
        
        return self._record_to_model(record, Session)

    async def iter_user_sessions(self, user_id: str) -> AsyncIterator[Session]:
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Order by created_at DESC matching the Mongo behavior
                    async for record in conn.cursor(
                        "SELECT * FROM sessions WHERE user_id = $1 ORDER BY created_at DESC", 
                        user_id
                    ):
                        yield self._record_to_model(record, Session)
        except Exception as exc:
            raise DbError(f"Failed to iterate user sessions: {exc}") from exc
