from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from exceptions import SessionNotFoundError, DbError
from models.db_schema import Session
from .base_repository import PostgresBaseRepository, SessionRow
from ..interfaces.session_repository import SessionRepository


class PostgresSessionRepository(PostgresBaseRepository, SessionRepository):
    """PostgreSQL implementation of SessionRepository.

    The SQLAlchemy session is called ``db`` here, and everywhere else in this
    package, because ``session`` already means a chat session in this app.
    """

    async def create_session(self, session: Session) -> str:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    insert(SessionRow)
                    .values(
                        id=self._generate_id(),
                        session_id=session.session_id,
                        user_id=session.user_id,
                        # Used to be dropped on the floor: the model had a
                        # title, the table had no column for it, so every read
                        # handed back the default.
                        title=session.title,
                        created_at=session.created_at,
                        updated_at=session.updated_at,
                    )
                    .on_conflict_do_nothing(index_elements=["session_id"])
                )
            return session.session_id
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create session: {exc}") from exc

    async def get_session(self, session_id: str) -> Session:
        try:
            async with self.session_factory() as db:
                row = await db.scalar(
                    select(SessionRow).where(SessionRow.session_id == session_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to get session: {exc}") from exc

        if row is None:
            raise SessionNotFoundError(f"Session {session_id!r} not found")

        return self._record_to_model(row, Session)

    async def iter_user_sessions(self, user_id: str) -> AsyncIterator[Session]:
        try:
            async with self.session_factory() as db:
                # Newest first, matching the Mongo backend.
                result = await db.stream_scalars(
                    select(SessionRow)
                    .where(SessionRow.user_id == user_id)
                    .order_by(SessionRow.created_at.desc())
                )
                async for row in result:
                    yield self._record_to_model(row, Session)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to iterate user sessions: {exc}") from exc
