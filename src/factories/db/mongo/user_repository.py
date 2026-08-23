
from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ASCENDING, ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import DbError, UserNotFoundError

from ..interfaces.user_repository import UserRepository
from .base_model import BaseModel
from models.db_schema import User
from models.db_schema.project import utcnow


class MongoUserRepository(UserRepository, BaseModel):
    """The users collection. Identity is an opaque id — nothing to verify."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.USERS)

    async def create_user(self, user: User) -> ObjectId:

        try:
            result = await self.collection.insert_one(user.model_dump(by_alias=True))

        except PyMongoError as exc:
            raise DbError(f"Could not create user {user.user_id!r}") from exc

        self.logger.info("Created user %r (_id=%s)", user.user_id, result.inserted_id)

        return result.inserted_id

    async def iter_users(self) -> AsyncIterator[User]:
        """Every user, oldest first.

        There is no auth, so "who am I" is a picker rather than a login. The
        list is the whole users collection, which on a local install is the
        handful of profiles someone made for themselves.
        """
        cursor = self.collection.find().sort("created_at", ASCENDING)

        try:
            async for document in cursor:
                yield User(**document)

        except PyMongoError as exc:
            raise DbError("Could not read users") from exc

    async def count_users(self) -> int:
        """Used to name the next one, so nobody has to type a label."""

        try:
            return await self.collection.count_documents({})
        except PyMongoError as exc:
            raise DbError("Could not count users") from exc

    async def rename(self, user_id: str, label: str) -> None:
        await self.patch_one(
            {"user_id": user_id},
            {"label": label},
            missing=UserNotFoundError,
            what=f"user {user_id!r}",
        )
        self.logger.info("Renamed user %r to %r", user_id, label)

    async def get_user(self, user_id: str) -> User:

        try:
            document = await self.collection.find_one({"user_id": user_id})

        except PyMongoError as exc:
            raise DbError(f"Could not read user {user_id!r}") from exc

        if document is None:
            raise UserNotFoundError(f"User {user_id!r} not found")

        return User(**document)
