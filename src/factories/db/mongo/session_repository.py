
from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import DESCENDING  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import SessionNotFoundError, DbError

from ..interfaces.session_repository import SessionRepository
from .base_model import BaseModel
from models.db_schema import Session
from models.db_schema.project import utcnow


class MongoSessionRepository(SessionRepository, BaseModel):
    """The sessions collection. A session groups a user's chats."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.SESSIONS)

    async def create_session(self, session: Session) -> ObjectId:

        try:
            result = await self.collection.insert_one(session.model_dump(by_alias=True))

        except PyMongoError as exc:
            raise DbError(
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
            raise DbError(f"Could not read session {session_id!r}") from exc

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
            raise DbError(f"Could not read sessions for user {user_id!r}") from exc

    async def delete_sessions_for_user(self, user_id: str) -> int:
        """Drop every session belonging to a user."""
        try:
            result = await self.collection.delete_many({"user_id": user_id})
        except PyMongoError as exc:
            raise DbError(f"Could not delete sessions for user {user_id!r}") from exc

        self.logger.info(
            "Deleted %d session(s) for user %r", result.deleted_count, user_id
        )
        return result.deleted_count
