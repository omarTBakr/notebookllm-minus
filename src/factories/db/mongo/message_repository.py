"""Data access for the chat feature: users, sessions, chats, messages.

One module for four small models, matching how their documents are grouped in
db_schema/conversation.py. Each follows ProjectModel: every PyMongoError is
re-raised as DbError, absent rows raise a typed NotFoundError, and reads
that can return many rows are async generators.
"""

from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ASCENDING, DESCENDING  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import DbError

from ..interfaces.message_repository import MessageRepository
from .base_model import BaseModel
from models.db_schema import Message


class MongoMessageRepository(MessageRepository, BaseModel):
    """The messages collection: the turns of every conversation."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.MESSAGES)

    async def create_message(self, message: Message) -> ObjectId:

        try:
            result = await self.collection.insert_one(message.model_dump(by_alias=True))

        except PyMongoError as exc:
            raise DbError(
                f"Could not create message in chat {message.chat_id!r}"
            ) from exc

        self.logger.debug(
            "Stored %s message in chat %r (%d chars)",
            message.role.value,
            message.chat_id,
            len(message.content),
        )

        return result.inserted_id

    async def iter_chat_messages(self, chat_id: str) -> AsyncIterator[Message]:
        """Every message in a chat, oldest first — reading order."""

        cursor = self.collection.find({"chat_id": chat_id}).sort("created_at", ASCENDING)

        try:
            async for document in cursor:
                yield Message(**document)

        except PyMongoError as exc:
            raise DbError(f"Could not read messages for chat {chat_id!r}") from exc

    async def get_recent_history(self, chat_id: str, limit: int) -> list[dict]:
        """The last *limit* turns, oldest-first, in provider-neutral form.

        Fetched newest-first so the cap keeps the most recent turns, then
        reversed — sorting ascending and slicing would keep the *oldest* ones
        and drop the context that actually matters.
        """
        cursor = (
            self.collection.find(
                {"chat_id": chat_id}, projection={"role": 1, "content": 1, "_id": 0}
            )
            .sort("created_at", DESCENDING)
            .limit(limit)
        )

        try:
            documents = [document async for document in cursor]

        except PyMongoError as exc:
            raise DbError(f"Could not read history for chat {chat_id!r}") from exc

        documents.reverse()

        return [{"role": d["role"], "content": d["content"]} for d in documents]

    async def delete_messages_for_chat(self, chat_id: str) -> int:
        """Drop a chat's whole transcript."""
        try:
            result = await self.collection.delete_many({"chat_id": chat_id})
        except PyMongoError as exc:
            raise DbError(f"Could not delete messages for chat {chat_id!r}") from exc

        self.logger.info(
            "Deleted %d message(s) for chat %r", result.deleted_count, chat_id
        )
        return result.deleted_count
