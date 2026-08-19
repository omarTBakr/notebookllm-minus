"""Data access for the chat feature: users, sessions, chats, messages.

One module for four small models, matching how their documents are grouped in
db_schema/conversation.py. Each follows ProjectModel: every PyMongoError is
re-raised as StorageError, absent rows raise a typed NotFoundError, and reads
that can return many rows are async generators.
"""

from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ASCENDING, DESCENDING, ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import ChatNotFoundError, SessionNotFoundError, StorageError, UserNotFoundError

from .BaseModel import BaseModel
from .db_schema import Chat, Message, Session, User
from .db_schema.project import utcnow


class UserModel(BaseModel):
    """The users collection. Identity is an opaque id — nothing to verify."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.USERS)

    async def create_user(self, user: User) -> ObjectId:

        try:
            result = await self.collection.insert_one(user.model_dump(by_alias=True))

        except PyMongoError as exc:
            raise StorageError(f"Could not create user {user.user_id!r}") from exc

        self.logger.info("Created user %r (_id=%s)", user.user_id, result.inserted_id)

        return result.inserted_id

    async def get_user(self, user_id: str) -> User:

        try:
            document = await self.collection.find_one({"user_id": user_id})

        except PyMongoError as exc:
            raise StorageError(f"Could not read user {user_id!r}") from exc

        if document is None:
            raise UserNotFoundError(f"User {user_id!r} not found")

        return User(**document)


class SessionModel(BaseModel):
    """The sessions collection. A session groups a user's chats."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.SESSIONS)

    async def create_session(self, session: Session) -> ObjectId:

        try:
            result = await self.collection.insert_one(session.model_dump(by_alias=True))

        except PyMongoError as exc:
            raise StorageError(
                f"Could not create session {session.session_id!r}"
            ) from exc

        self.logger.info(
            "Created session %r for user %r", session.session_id, session.user_id
        )

        return result.inserted_id

    async def get_session(self, session_id: str) -> Session:

        try:
            document = await self.collection.find_one({"session_id": session_id})

        except PyMongoError as exc:
            raise StorageError(f"Could not read session {session_id!r}") from exc

        if document is None:
            raise SessionNotFoundError(f"Session {session_id!r} not found")

        return Session(**document)

    async def iter_user_sessions(self, user_id: str) -> AsyncIterator[Session]:
        """Every session belonging to a user, newest first."""

        # find() builds a cursor synchronously — do NOT await it.
        cursor = self.collection.find({"user_id": user_id}).sort("created_at", DESCENDING)

        try:
            async for document in cursor:
                yield Session(**document)

        except PyMongoError as exc:
            raise StorageError(f"Could not read sessions for user {user_id!r}") from exc

    async def touch(self, session_id: str) -> None:
        """Bump updated_at so the sidebar can order by recent activity."""

        try:
            await self.collection.update_one(
                {"session_id": session_id}, {"$set": {"updated_at": utcnow()}}
            )

        except PyMongoError as exc:
            raise StorageError(f"Could not touch session {session_id!r}") from exc


class ChatModel(BaseModel):
    """The chats collection. One chat is one conversation and one document space."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.CHATS)

    async def create_chat(self, chat: Chat) -> ObjectId:

        try:
            result = await self.collection.insert_one(chat.model_dump(by_alias=True))

        except PyMongoError as exc:
            raise StorageError(f"Could not create chat {chat.chat_id!r}") from exc

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
            raise StorageError(f"Could not read chat {chat_id!r}") from exc

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
            raise StorageError(
                f"Could not read chats for session {session_id!r}"
            ) from exc

    async def set_has_documents(self, chat_id: str, value: bool = True) -> None:
        """Flag a chat as having documents, for the sidebar badge."""

        try:
            result = await self.collection.find_one_and_update(
                {"chat_id": chat_id},
                {"$set": {"has_documents": value, "updated_at": utcnow()}},
                projection={"_id": 1},
                return_document=ReturnDocument.AFTER,
            )

        except PyMongoError as exc:
            raise StorageError(
                f"Could not update has_documents on chat {chat_id!r}"
            ) from exc

        if result is None:
            raise ChatNotFoundError(f"Chat {chat_id!r} not found")

    async def set_models(
        self,
        chat_id: str,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> None:
        """Point a chat at specific models. Only the fields given are touched."""

        changes: dict = {"updated_at": utcnow()}

        if generation_model is not None:
            changes["generation_model"] = generation_model

        if embedding_model is not None:
            changes["embedding_model"] = embedding_model
            changes["embedding_dimensions"] = embedding_dimensions

        try:
            result = await self.collection.find_one_and_update(
                {"chat_id": chat_id},
                {"$set": changes},
                projection={"_id": 1},
                return_document=ReturnDocument.AFTER,
            )

        except PyMongoError as exc:
            raise StorageError(f"Could not set models on chat {chat_id!r}") from exc

        if result is None:
            raise ChatNotFoundError(f"Chat {chat_id!r} not found")

        self.logger.info(
            "Chat %r models set (generation=%r, embedding=%r)",
            chat_id,
            generation_model,
            embedding_model,
        )

    async def rename(self, chat_id: str, title: str) -> None:
        """Set the chat's title — used to name a chat after its first question."""

        try:
            result = await self.collection.find_one_and_update(
                {"chat_id": chat_id},
                {"$set": {"title": title, "updated_at": utcnow()}},
                projection={"_id": 1},
                return_document=ReturnDocument.AFTER,
            )

        except PyMongoError as exc:
            raise StorageError(f"Could not rename chat {chat_id!r}") from exc

        if result is None:
            raise ChatNotFoundError(f"Chat {chat_id!r} not found")


class MessageModel(BaseModel):
    """The messages collection: the turns of every conversation."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.MESSAGES)

    async def create_message(self, message: Message) -> ObjectId:

        try:
            result = await self.collection.insert_one(message.model_dump(by_alias=True))

        except PyMongoError as exc:
            raise StorageError(
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
            raise StorageError(f"Could not read messages for chat {chat_id!r}") from exc

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
            raise StorageError(f"Could not read history for chat {chat_id!r}") from exc

        documents.reverse()

        return [{"role": d["role"], "content": d["content"]} for d in documents]

    async def delete_chat_messages(self, chat_id: str) -> int:

        try:
            result = await self.collection.delete_many({"chat_id": chat_id})

        except PyMongoError as exc:
            raise StorageError(
                f"Could not delete messages for chat {chat_id!r}"
            ) from exc

        return result.deleted_count
