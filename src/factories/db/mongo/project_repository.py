from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from pymongo import ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import ProjectNotFoundError, DbError
from ..interfaces.project_repository import ProjectRepository
from .base_model import BaseModel
from models.db_schema import Project
from models.db_schema.project import utcnow

from typing import AsyncIterator


class MongoProjectRepository(ProjectRepository, BaseModel):
    """Data access for the projects collection.

    Raises typed errors and lets them propagate; deciding what a failure means
    for the caller (a 404, a retry, a fallback) is not this layer's job.
    """

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.PROJECTS)

    async def create_project(self, project: Project) -> ObjectId:
        try:
            # by_alias writes the model's id as `_id`, so the document has one
            # identifier rather than an `id` field plus a generated `_id`.
            result = await self.collection.insert_one(project.model_dump(by_alias=True))
        except PyMongoError as exc:
            raise DbError(
                f"Could not create project {project.project_id!r}"
            ) from exc

        self.logger.info(
            "Created project %r (_id=%s)", project.project_id, result.inserted_id
        )
        return result.inserted_id

    async def get_project(self, project_id: str) -> Project:
        try:
            result = await self.collection.find_one({"project_id": project_id})
        except PyMongoError as exc:
            raise DbError(f"Could not read project {project_id!r}") from exc

        if result is None:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

        self.logger.debug("Fetched project %r", project_id)
        return Project(**result)

    async def update_project(self, project: Project) -> ObjectId:
        """Update the project, creating it if absent. Always returns its _id.

        Returning the id unconditionally is deliberate: the previous version
        returned None on the update path, which callers could not tell apart
        from a failure. Failure is signalled by DbError, nothing else.
        """
        project.updated_at = utcnow()
        # `_id` is immutable in MongoDB so it must stay out of $set, and
        # created_at must not be rewritten on every update — both belong in
        # $setOnInsert, which only applies when this upsert actually inserts.
        #
        # chunks_ids/assets_ids are excluded for the same reason: they are owned
        # by the $addToSet helpers below, and a caller building a fresh Project
        # to upsert carries empty lists that would otherwise wipe both arrays on
        # every single call.
        changes = project.model_dump(
            by_alias=True,
            exclude={"id", "created_at", "chunks_ids", "assets_ids"},
        )
        on_insert = {
            "_id": project.id,
            "created_at": project.created_at,
            "chunks_ids": project.chunks_ids,
            "assets_ids": project.assets_ids,
        }

        try:
            document = await self.collection.find_one_and_update(
                {"project_id": project.project_id},
                {"$set": changes, "$setOnInsert": on_insert},
                upsert=True,
                projection={"_id": 1},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not update project {project.project_id!r}"
            ) from exc

        object_id = document["_id"]
        self.logger.info("Saved project %r (_id=%s)", project.project_id, object_id)
        return object_id

    async def add_asset_id(self, project_id: str, asset_object_id: ObjectId) -> None:
        """Append *asset_object_id* to the project's assets_ids list.

        Uses $addToSet so re-uploading the same asset never creates duplicates.
        Raises ProjectNotFoundError if the project doesn't exist yet.
        """
        try:
            result = await self.collection.update_one(
                {"project_id": project_id},
                {
                    "$addToSet": {"assets_ids": asset_object_id},
                    "$set": {"updated_at": utcnow()},
                },
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not add asset_id to project {project_id!r}"
            ) from exc

        if result.matched_count == 0:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

        self.logger.debug(
            "Added asset %s to project %r assets_ids", asset_object_id, project_id
        )

    async def add_chunk_ids(
        self, project_id: str, chunk_object_ids: list[ObjectId]
    ) -> None:
        """Append every id in *chunk_object_ids* to the project's chunks_ids list.

        Uses $addToSet with $each so multiple IDs are added in a single round
        trip and existing IDs are never duplicated.
        Raises ProjectNotFoundError if the project doesn't exist yet.
        """
        if not chunk_object_ids:
            return

        try:
            result = await self.collection.update_one(
                {"project_id": project_id},
                {
                    "$addToSet": {"chunks_ids": {"$each": chunk_object_ids}},
                    "$set": {"updated_at": utcnow()},
                },
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not add chunk_ids to project {project_id!r}"
            ) from exc

        if result.matched_count == 0:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

        self.logger.debug(
            "Added %d chunk id(s) to project %r", len(chunk_object_ids), project_id
        )

    async def remove_chunk_ids(
        self, project_id: str, chunk_object_ids: list[ObjectId]
    ) -> None:
        """Pull specific ids out of chunks_ids, leaving the rest in place.

        What a single asset's re-ingest needs: clear_chunk_ids() empties the
        whole list, which is only correct when the entire project is being
        rebuilt.
        """
        if not chunk_object_ids:
            return

        try:
            result = await self.collection.update_one(
                {"project_id": project_id},
                {
                    "$pullAll": {"chunks_ids": chunk_object_ids},
                    "$set": {"updated_at": utcnow()},
                },
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not remove chunk_ids from project {project_id!r}"
            ) from exc

        if result.matched_count == 0:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

        self.logger.debug(
            "Removed %d chunk id(s) from project %r", len(chunk_object_ids), project_id
        )

    async def clear_chunk_ids(self, project_id: str) -> None:
        """Empty the project's chunks_ids list (used before a reset re-ingest)."""
        try:
            await self.collection.update_one(
                {"project_id": project_id},
                {
                    "$set": {"chunks_ids": [], "updated_at": utcnow()},
                },
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not clear chunk_ids for project {project_id!r}"
            ) from exc

        self.logger.debug("Cleared chunks_ids for project %r", project_id)

    async def list_projects(self) -> list[Project]:
        """Every project, newest first."""
        try:
            cursor = self.collection.find({})
            return [Project(**document) async for document in cursor.sort("created_at", -1)]
        except PyMongoError as exc:
            raise DbError("Could not list projects") from exc

    async def rename(self, project_id: str, name: str) -> None:
        """Change a project's display name."""
        await self.patch_one(
            {"project_id": project_id},
            {"name": name},
            missing=ProjectNotFoundError,
            what=f"project {project_id!r}",
        )
        self.logger.info("Renamed project %r to %r", project_id, name)

    async def delete_project(self, project_id: str) -> None:
        """Remove the project row itself. Assets and chunks are separate."""
        try:
            result = await self.collection.delete_many({"project_id": project_id})
        except PyMongoError as exc:
            raise DbError(f"Could not delete project {project_id!r}") from exc

        if not result.deleted_count:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

        self.logger.info("Deleted project %r", project_id)
