from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from pymongo import ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import ProjectNotFoundError, StorageError
from .BaseModel import BaseModel
from .db_schema import Project
from .db_schema.project import utcnow

from typing import AsyncIterator


class ProjectModel(BaseModel):
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
            raise StorageError(
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
            raise StorageError(f"Could not read project {project_id!r}") from exc

        if result is None:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

        self.logger.debug("Fetched project %r", project_id)
        return Project(**result)

    async def update_project(self, project: Project) -> ObjectId:
        """Update the project, creating it if absent. Always returns its _id.

        Returning the id unconditionally is deliberate: the previous version
        returned None on the update path, which callers could not tell apart
        from a failure. Failure is signalled by StorageError, nothing else.
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
            raise StorageError(
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
            raise StorageError(
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
            raise StorageError(
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
            raise StorageError(
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
            raise StorageError(
                f"Could not clear chunk_ids for project {project_id!r}"
            ) from exc

        self.logger.debug("Cleared chunks_ids for project %r", project_id)


    async def _get_all_projects(self) -> AsyncIterator[Project]:
        cursor = (
            self.collection.find()
        )  # find() is synchronous in Motor — do NOT await it
        try:
            async for doc in cursor:
                yield Project(**doc)
        except PyMongoError as exc:
            raise StorageError("Could not read projects") from exc

        self.logger.debug("Fetched all projects")

    async def get_all_projects_by_name(self, name: str) -> AsyncIterator[Project]:
        cursor = self.collection.find(
            {"name": name}
        )  # find() is synchronous in Motor — do NOT await it
        try:
            async for doc in cursor:
                yield Project(**doc)
        except PyMongoError as exc:
            raise StorageError("Could not read projects") from exc

        self.logger.debug("Fetched all projects by name=%r", name)

    async def get_all_projects(
        self,
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> AsyncIterator[Project]:
        try:
            # find() builds a cursor synchronously — awaiting it raises
            # TypeError, as the comments on the two methods above note.
            result = (
                self.collection.find()
                .sort(sort_by, 1 if sort_order == "asc" else -1)
                .skip((page - 1) * page_size)
                .limit(page_size)
            )
        except PyMongoError as exc:
            raise StorageError("Could not read projects") from exc

        self.logger.debug("Fetched all projects")
        async for doc in result:
            yield Project(**doc)

    def get_index(
        self,
        keys: list[tuple[str, int]],   # e.g. [("project_id", 1), ("created_at", -1)]
        unique: bool = False,
        name: str | None = None,
    ) -> dict:
        """Build an index spec dict for create_index.
        
        keys: list of (field, direction) tuples — 1=ASC, -1=DESC
        """
        auto_name = "_".join(
            f"{field}_{'asc' if dir == 1 else 'desc'}" for field, dir in keys
        ) + "_idx"

        return {
            "key": keys,
            "name": name or auto_name,
            "unique": unique,
        }

    async def create_index(
        self,
        keys: list[tuple[str, int]],
        unique: bool = False,
        name: str | None = None,
    ) -> str:
        index = self.get_index(keys, unique, name)
        return await self.collection.create_index(index["key"], name=index["name"], unique=index["unique"])

    async def get_assets_for_project(self, project_id: str) -> list[ObjectId]:
        result = await self.collection.find_one({"project_id": project_id})
        if not result:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")
        return result["assets_ids"]
    

    
    