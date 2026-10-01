from typing import AsyncIterator

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from data.models import PersonalUserInfo
from shared.exceptions import DbError

from ..interfaces.personal_user_info_repository import PersonalUserInfoRepository
from .base_repository import PersonalUserInfoRow, PostgresBaseRepository


class PostgresPersonalUserInfoRepository(PostgresBaseRepository, PersonalUserInfoRepository):
    """PostgreSQL implementation of PersonalUserInfoRepository."""

    async def upsert_fact(self, user_id: str, key: str, description: str) -> str:
        statement = (
            insert(PersonalUserInfoRow)
            .values(
                id=self._generate_id(),
                user_id=self._scrub(user_id),
                key=self._scrub(key),
                description=self._scrub(description),
            )
            .on_conflict_do_update(
                index_elements=["user_id", "key"],
                set_={
                    "description": self._scrub(description),
                    "updated_at": func.now(),
                },
            )
            .returning(PersonalUserInfoRow.id)
        )

        try:
            async with self.session_factory.begin() as db:
                return await db.scalar(statement)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to upsert personal_user_information for {user_id!r}/{key!r}: {exc}") from exc

    async def list_facts(self, user_id: str) -> AsyncIterator[PersonalUserInfo]:
        try:
            async with self.session_factory() as db:
                result = await db.stream_scalars(
                    select(PersonalUserInfoRow).where(PersonalUserInfoRow.user_id == user_id)
                )
                async for row in result:
                    yield self._record_to_model(row, PersonalUserInfo)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to list personal_user_information for {user_id!r}: {exc}") from exc

    async def delete_fact(self, user_id: str, key: str) -> bool:
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    delete(PersonalUserInfoRow).where(
                        PersonalUserInfoRow.user_id == user_id,
                        PersonalUserInfoRow.key == key,
                    )
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete personal_user_information {user_id!r}/{key!r}: {exc}") from exc

        return result.rowcount > 0
