from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ASCENDING  # ty: ignore[unresolved-import]
from pymongo.errors import (  # ty: ignore[unresolved-import]
    DuplicateKeyError,
    PyMongoError,
)

from data.models import User
from shared.enums import DatabaseCollection
from shared.exceptions import DbError, DuplicateEmailError, UserNotFoundError

from ..interfaces.user_repository import UserRepository
from .base_model import BaseModel


class MongoUserRepository(UserRepository, BaseModel):
    """The users collection: accounts, keyed by an opaque user_id."""

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
        """Every user, oldest first. For maintenance; no route lists users."""
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

    async def get_user_by_email(self, email: str) -> User | None:
        try:
            document = await self.collection.find_one({"email": email})
        except PyMongoError as exc:
            raise DbError("Could not look up a user by email") from exc

        return User(**document) if document is not None else None

    async def set_credentials(self, user_id: str, email: str, password_hash: str) -> None:
        try:
            await self.patch_one(
                {"user_id": user_id},
                {"email": email, "password_hash": password_hash},
                missing=UserNotFoundError,
                what=f"user {user_id!r}",
            )
        except DbError as exc:
            if isinstance(exc.__cause__, DuplicateKeyError):
                raise DuplicateEmailError("An account with this email already exists") from exc
            raise

    async def set_password_hash(self, user_id: str, password_hash: str) -> None:
        await self.patch_one(
            {"user_id": user_id},
            {"password_hash": password_hash},
            missing=UserNotFoundError,
            what=f"user {user_id!r}",
        )

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

    async def delete_user(self, user_id: str) -> bool:
        """Remove one user. The caller clears what hangs off them first."""
        try:
            result = await self.collection.delete_one({"user_id": user_id})
        except PyMongoError as exc:
            raise DbError(f"Could not delete user {user_id!r}") from exc

        if result.deleted_count:
            self.logger.info("Deleted user %r", user_id)

        return result.deleted_count > 0
