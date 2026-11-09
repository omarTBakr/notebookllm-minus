from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from data.models import AuthSession
from shared.enums import DatabaseCollection
from shared.exceptions import DbError

from ..interfaces.auth_session_repository import AuthSessionRepository
from .base_model import BaseModel


class MongoAuthSessionRepository(AuthSessionRepository, BaseModel):
    """Signed-in browsers, by the sha256 of their cookie token."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.AUTH_SESSIONS)

    async def create(self, session: AuthSession) -> None:
        try:
            await self.collection.insert_one(session.model_dump(by_alias=True))
        except PyMongoError as exc:
            raise DbError("Could not create an auth session") from exc

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        try:
            document = await self.collection.find_one({"token_hash": token_hash})
        except PyMongoError as exc:
            raise DbError("Could not read an auth session") from exc
        return AuthSession(**document) if document is not None else None

    async def touch(self, token_hash: str, last_seen_at: datetime, expires_at: datetime) -> None:
        try:
            await self.collection.update_one(
                {"token_hash": token_hash},
                {"$set": {"last_seen_at": last_seen_at, "expires_at": expires_at}},
            )
        except PyMongoError as exc:
            raise DbError("Could not refresh an auth session") from exc

    async def delete(self, token_hash: str) -> bool:
        try:
            result = await self.collection.delete_one({"token_hash": token_hash})
        except PyMongoError as exc:
            raise DbError("Could not delete an auth session") from exc
        return result.deleted_count > 0

    async def delete_for_user(self, user_id: str, keep_token_hash: str | None = None) -> int:
        filt: dict = {"user_id": user_id}
        if keep_token_hash:
            filt["token_hash"] = {"$ne": keep_token_hash}
        try:
            result = await self.collection.delete_many(filt)
        except PyMongoError as exc:
            raise DbError("Could not delete auth sessions") from exc
        return result.deleted_count

    async def delete_expired(self, now: datetime) -> int:
        try:
            result = await self.collection.delete_many({"expires_at": {"$lte": now}})
        except PyMongoError as exc:
            raise DbError("Could not sweep expired auth sessions") from exc
        return result.deleted_count
