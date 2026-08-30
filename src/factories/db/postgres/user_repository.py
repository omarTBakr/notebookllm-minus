from typing import AsyncIterator

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from exceptions import UserNotFoundError, DbError
from models.db_schema import User
from .base_repository import PostgresBaseRepository, UserRow
from ..interfaces.user_repository import UserRepository


class PostgresUserRepository(PostgresBaseRepository, UserRepository):
    """PostgreSQL implementation of UserRepository."""

    async def create_user(self, user: User) -> str:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    insert(UserRow)
                    .values(
                        id=self._generate_id(),
                        user_id=user.user_id,
                        label=user.label,
                        created_at=user.created_at,
                        updated_at=user.updated_at,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id"])
                )
            return user.user_id
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create user: {exc}") from exc

    async def get_user(self, user_id: str) -> User:
        try:
            async with self.session_factory() as db:
                row = await db.scalar(
                    select(UserRow).where(UserRow.user_id == user_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to get user: {exc}") from exc

        if row is None:
            raise UserNotFoundError(f"User {user_id!r} not found")

        return self._record_to_model(row, User)

    async def rename(self, user_id: str, label: str) -> None:
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    update(UserRow)
                    .where(UserRow.user_id == user_id)
                    .values(label=label, updated_at=func.now())
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to rename user: {exc}") from exc

        if result.rowcount == 0:
            raise UserNotFoundError(f"User {user_id!r} not found")

    async def count_users(self) -> int:
        try:
            async with self.session_factory() as db:
                return await db.scalar(select(func.count()).select_from(UserRow))
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to count users: {exc}") from exc

    async def iter_users(self) -> AsyncIterator[User]:
        try:
            # stream_scalars keeps this a server-side cursor: the whole users
            # table never has to fit in memory at once.
            async with self.session_factory() as db:
                result = await db.stream_scalars(select(UserRow))
                async for row in result:
                    yield self._record_to_model(row, User)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to iterate users: {exc}") from exc

    async def delete_user(self, user_id: str) -> bool:
        """Remove one user row. The caller clears what hangs off them first."""
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    delete(UserRow).where(UserRow.user_id == user_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete user: {exc}") from exc

        return result.rowcount > 0
