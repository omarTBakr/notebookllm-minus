
from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import DESCENDING, ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import ChatNotFoundError, DbError

from ..interfaces.chat_repository import ChatRepository
from .base_model import BaseModel
from models.db_schema import Chat
from models.db_schema.project import utcnow


class MongoChatRepository(ChatRepository, BaseModel):
    """The chats collection. One chat is one conversation and one document space."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.CHATS)

    async def create_chat(self, chat: Chat) -> ObjectId:

        try:
            result = await self.collection.insert_one(chat.model_dump(by_alias=True))

        except PyMongoError as exc:
            raise DbError(f"Could not create chat {chat.chat_id!r}") from exc

        self.logger.info(
            "Created chat %r in session %r (lang=%s)",
            chat.chat_id,
            chat.session_id,
            chat.lang,
        )

        return result.inserted_id

    async def get_chat(self, chat_id: str) -> Chat:

        try:
            document = await self.collection.find_one({"chat_id": chat_id})

        except PyMongoError as exc:
            raise DbError(f"Could not read chat {chat_id!r}") from exc

        if document is None:
            raise ChatNotFoundError(f"Chat {chat_id!r} not found")

        return Chat(**document)

    async def iter_session_chats(self, session_id: str) -> AsyncIterator[Chat]:
        """Every chat in a session, newest first."""

        cursor = self.collection.find({"session_id": session_id}).sort(
            "created_at", DESCENDING
        )

        try:
            async for document in cursor:
                yield Chat(**document)

        except PyMongoError as exc:
            raise DbError(
                f"Could not read chats for session {session_id!r}"
            ) from exc

    async def set_has_documents(self, chat_id: str, has_documents: bool) -> None:
        await self.patch_one(
            {"chat_id": chat_id},
            {"has_documents": has_documents},
            missing=ChatNotFoundError,
            what=f"chat {chat_id!r}",
        )

    async def set_models(
        self,
        chat_id: str,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> None:
        """Point a chat at specific models. Only the fields given are touched."""
        # None means "leave it alone", not "clear it".
        changes = {
            "generation_model": generation_model,
            "embedding_model": embedding_model,
            "embedding_dimensions": embedding_dimensions,
        }
        changes = {k: v for k, v in changes.items() if v is not None}

        if not changes:
            return

        await self.patch_one(
            {"chat_id": chat_id},
            changes,
            missing=ChatNotFoundError,
            what=f"chat {chat_id!r}",
        )

    async def set_settings(self, chat_id: str, changes: dict) -> None:
        """Apply the caller's settings dict verbatim."""
        if not changes:
            return

        await self.patch_one(
            {"chat_id": chat_id},
            dict(changes),
            missing=ChatNotFoundError,
            what=f"chat {chat_id!r}",
        )

    async def iter_user_chats(self, user_id: str) -> AsyncIterator[Chat]:
        """Every chat a user owns, across all their sessions."""

        cursor = self.collection.find({"user_id": user_id}).sort("created_at", DESCENDING)

        try:
            async for document in cursor:
                yield Chat(**document)

        except PyMongoError as exc:
            raise DbError(f"Could not read chats for user {user_id!r}") from exc

    async def rename(self, chat_id: str, title: str) -> None:
        await self.patch_one(
            {"chat_id": chat_id},
            {"title": title},
            missing=ChatNotFoundError,
            what=f"chat {chat_id!r}",
        )
        self.logger.info("Renamed chat %r to %r", chat_id, title)

    async def delete_chat(self, chat_id: str) -> bool:
        """Remove one chat. The caller clears what hangs off it first."""
        try:
            result = await self.collection.delete_one({"chat_id": chat_id})
        except PyMongoError as exc:
            raise DbError(f"Could not delete chat {chat_id!r}") from exc

        if result.deleted_count:
            self.logger.info("Deleted chat %r", chat_id)

        return result.deleted_count > 0
