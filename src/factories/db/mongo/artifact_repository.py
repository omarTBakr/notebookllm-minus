from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import DbError
from models.db_schema import Artifact
from models.db_schema.project import utcnow

from ..interfaces.artifact_repository import ArtifactRepository
from .base_model import BaseModel


class MongoArtifactRepository(ArtifactRepository, BaseModel):
    """Data access for the artifacts collection."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.ARTIFACTS)

    async def create_artifact(self, artifact: Artifact) -> str:
        # Upsert on (chat_id, kind): regenerating replaces the current set, and
        # the unique index makes a plain insert fail the second time.
        # Everything is reset -- a regeneration inheriting the previous run's
        # items would show old cards beside new ones with no way to tell them
        # apart.
        try:
            await self.collection.update_one(
                {"chat_id": artifact.chat_id, "kind": artifact.kind.value},
                {
                    "$set": {
                        "artifact_id": artifact.artifact_id,
                        "status": artifact.status.value,
                        "items": artifact.items,
                        "source_task_id": artifact.source_task_id,
                        "error": artifact.error,
                        "updated_at": utcnow(),
                    },
                    "$setOnInsert": {
                        "chat_id": artifact.chat_id,
                        "kind": artifact.kind.value,
                        "created_at": utcnow(),
                    },
                },
                upsert=True,
            )

            return artifact.artifact_id
        except PyMongoError as exc:
            raise DbError(f"Failed to create artifact: {exc}") from exc

    async def find_artifact(self, chat_id: str, kind: str) -> Artifact | None:
        try:
            document = await self.collection.find_one({"chat_id": chat_id, "kind": kind})

            return Artifact(**document) if document else None
        except PyMongoError as exc:
            raise DbError(f"Failed to read artifact: {exc}") from exc

    async def append_items(self, artifact_id: str, items: list[dict]) -> int:
        # $push/$each at the database, not a read-modify-write here. This runs
        # once per batch while the browser may already be reading the set, so
        # reading, appending and writing back would lose items to any
        # concurrent write and briefly serve a shorter deck than the one
        # already on screen.
        try:
            if not items:
                document = await self.collection.find_one({"artifact_id": artifact_id}, {"items": 1})
            else:
                document = await self.collection.find_one_and_update(
                    {"artifact_id": artifact_id},
                    {
                        "$push": {"items": {"$each": items}},
                        "$set": {"updated_at": utcnow()},
                    },
                    projection={"items": 1},
                    return_document=ReturnDocument.AFTER,
                )

            return len(document.get("items", [])) if document else 0
        except PyMongoError as exc:
            raise DbError(f"Failed to append artifact items: {exc}") from exc

    async def replace_items(self, artifact_id: str, items: list[dict]) -> int:
        try:
            await self.collection.update_one(
                {"artifact_id": artifact_id},
                {"$set": {"items": items, "updated_at": utcnow()}},
            )

            return len(items)
        except PyMongoError as exc:
            raise DbError(f"Failed to replace artifact items: {exc}") from exc

    async def finish_artifact(self, artifact_id: str, status: str, error: str = "") -> None:
        try:
            await self.collection.update_one(
                {"artifact_id": artifact_id},
                {"$set": {"status": status, "error": error, "updated_at": utcnow()}},
            )
        except PyMongoError as exc:
            raise DbError(f"Failed to finish artifact: {exc}") from exc

    async def delete_artifacts_for_chat(self, chat_id: str) -> int:
        try:
            result = await self.collection.delete_many({"chat_id": chat_id})

            return result.deleted_count
        except PyMongoError as exc:
            raise DbError(f"Failed to delete artifacts: {exc}") from exc
