from typing import AsyncIterator

from exceptions import UserNotFoundError, DbError
from models.db_schema import User
from .base_repository import PostgresBaseRepository
from ..interfaces.user_repository import UserRepository


class PostgresUserRepository(PostgresBaseRepository, UserRepository):
    """PostgreSQL implementation of UserRepository."""

    async def create_user(self, user: User) -> str:
        record_id = self._generate_id()
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO users (id, user_id, label, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    record_id,
                    user.user_id,
                    user.label,
                    user.created_at,
                    user.updated_at,
                )
            return user.user_id
        except Exception as exc:
            raise DbError(f"Failed to create user: {exc}") from exc

    async def get_user(self, user_id: str) -> User:
        try:
            async with self.pool.acquire() as conn:
                record = await conn.fetchrow(
                    "SELECT * FROM users WHERE user_id = $1", user_id
                )
        except Exception as exc:
            raise DbError(f"Failed to get user: {exc}") from exc

        if not record:
            raise UserNotFoundError(f"User {user_id!r} not found")
        
        return self._record_to_model(record, User)

    async def rename(self, user_id: str, label: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE users SET label = $1, updated_at = CURRENT_TIMESTAMP WHERE user_id = $2",
                    label,
                    user_id,
                )
                if result == "UPDATE 0":
                    raise UserNotFoundError(f"User {user_id!r} not found")
        except UserNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to rename user: {exc}") from exc

    async def count_users(self) -> int:
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchval("SELECT COUNT(*) FROM users")
        except Exception as exc:
            raise DbError(f"Failed to count users: {exc}") from exc

    async def iter_users(self) -> AsyncIterator[User]:
        try:
            # We use a transaction for the cursor
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    async for record in conn.cursor("SELECT * FROM users"):
                        yield self._record_to_model(record, User)
        except Exception as exc:
            raise DbError(f"Failed to iterate users: {exc}") from exc
