from typing import AsyncIterator

from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError

from exceptions import DbError
from models.db_schema import Message
from .base_repository import PostgresBaseRepository, MessageRow
from ..interfaces.message_repository import MessageRepository


class PostgresMessageRepository(PostgresBaseRepository, MessageRepository):
    """PostgreSQL implementation of MessageRepository."""

    async def create_message(self, message: Message) -> str:
        record_id = self._generate_id()
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    insert(MessageRow).values(
                        id=record_id,
                        message_id=message.message_id,
                        chat_id=message.chat_id,
                        role=message.role.value,
                        content=message.content,
                        citations=message.citations,
                        created_at=message.created_at,
                    )
                )
            # The model is constructed on the fly without a natural primary key
            # return string of ObjectId.
            return record_id
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create message: {exc}") from exc

    async def iter_chat_messages(self, chat_id: str) -> AsyncIterator[Message]:
        try:
            async with self.session_factory() as db:
                result = await db.stream_scalars(
                    select(MessageRow)
                    .where(MessageRow.chat_id == chat_id)
                    .order_by(MessageRow.created_at.asc())
                )
                async for row in result:
                    yield self._record_to_model(row, Message)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to iterate chat messages: {exc}") from exc

    async def get_recent_history(self, chat_id: str, limit: int) -> list[dict]:
        try:
            async with self.session_factory() as db:
                result = await db.execute(
                    select(MessageRow.role, MessageRow.content)
                    .where(MessageRow.chat_id == chat_id)
                    .order_by(MessageRow.created_at.desc())
                    .limit(limit)
                )
                rows = result.all()

            # We ordered DESC to get the latest `limit` messages, but chat models
            # need them in chronological order. Reverse them before returning.
            return [
                {"role": role, "content": content} for role, content in reversed(rows)
            ]
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to get recent history: {exc}") from exc
