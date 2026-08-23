import json
from typing import AsyncIterator

from exceptions import DbError
from models.db_schema import Message
from .base_repository import PostgresBaseRepository
from ..interfaces.message_repository import MessageRepository


class PostgresMessageRepository(PostgresBaseRepository, MessageRepository):
    """PostgreSQL implementation of MessageRepository."""

    async def create_message(self, message: Message) -> str:
        record_id = self._generate_id()
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO messages (id, message_id, chat_id, role, content, citations, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    record_id,
                    message.message_id,
                    message.chat_id,
                    message.role.value,
                    message.content,
                    json.dumps(message.citations),
                    message.created_at,
                )
            # The model is constructed on the fly without a natural primary key
            # return string of ObjectId.
            return record_id
        except Exception as exc:
            raise DbError(f"Failed to create message: {exc}") from exc

    async def iter_chat_messages(self, chat_id: str) -> AsyncIterator[Message]:
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    query = "SELECT * FROM messages WHERE chat_id = $1 ORDER BY created_at ASC"
                    async for record in conn.cursor(query, chat_id):
                        yield self._record_to_model(record, Message)
        except Exception as exc:
            raise DbError(f"Failed to iterate chat messages: {exc}") from exc

    async def get_recent_history(self, chat_id: str, limit: int) -> list[dict]:
        try:
            async with self.pool.acquire() as conn:
                query = """
                    SELECT role, content 
                    FROM messages 
                    WHERE chat_id = $1 
                    ORDER BY created_at DESC 
                    LIMIT $2
                """
                records = await conn.fetch(query, chat_id, limit)
                
                # We ordered DESC to get the latest `limit` messages, but chat models
                # need them in chronological order. Reverse them before returning.
                return [
                    {"role": r["role"], "content": r["content"]}
                    for r in reversed(records)
                ]
        except Exception as exc:
            raise DbError(f"Failed to get recent history: {exc}") from exc
