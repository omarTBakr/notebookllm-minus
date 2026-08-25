from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import DbError
from ..interfaces.asset_repository import AssetRepository
from .base_model import BaseModel
from models.db_schema.asset import Asset, utcnow
from exceptions import AssetNotFoundError



class MongoAssetRepository(AssetRepository, BaseModel):
    """Data access for the assets collection.

    Raises typed errors and lets them propagate; deciding what a failure means
    for the caller (a 404, a retry, a fallback) is not this layer's job.
    """

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.ASSETS)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_asset(self, asset: Asset) -> ObjectId:
        """Insert a new asset document. Returns the inserted ``_id``."""
        try:
            # by_alias writes the model's id as `_id`, so the document has one
            # identifier rather than an `id` field plus a generated `_id`.
            result = await self.collection.insert_one(asset.model_dump(by_alias=True))
        except PyMongoError as exc:
            raise DbError(
                f"Could not create asset {asset.asset_id!r}"
            ) from exc

        self.logger.info(
            "Created asset %r (_id=%s)", asset.asset_id, result.inserted_id
        )
        return result.inserted_id

    async def update_asset(self, asset: Asset) -> ObjectId:
        """Upsert an asset document. Always returns its ``_id``.

        Returning the id unconditionally is deliberate: the previous version
        returned None on the update path, which callers could not tell apart
        from a failure. Failure is signalled by DbError, nothing else.
        """
        asset.updated_at = utcnow()
        # `_id` is immutable in MongoDB so it must stay out of $set, and
        # created_at must not be rewritten on every update — both belong in
        # $setOnInsert, which only applies when this upsert actually inserts.
        changes = asset.model_dump(by_alias=True, exclude={"id", "created_at"})
        on_insert = {"_id": asset.id, "created_at": asset.created_at}

        try:
            document = await self.collection.find_one_and_update(
                {"asset_id": asset.asset_id},
                {"$set": changes, "$setOnInsert": on_insert},
                upsert=True,
                projection={"_id": 1},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not update asset {asset.asset_id!r}"
            ) from exc

        object_id = document["_id"]
        self.logger.info("Saved asset %r (_id=%s)", asset.asset_id, object_id)
        return object_id

    async def rename(self, asset_id: str, name: str) -> None:
        """Change an asset's display name. Nothing else about it moves."""
        await self.patch_one(
            {"asset_id": asset_id},
            {"name": name},
            missing=AssetNotFoundError,
            what=f"asset {asset_id!r}",
        )
        self.logger.info("Renamed asset %r to %r", asset_id, name)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_asset(self, asset_id: str) -> Asset:
        """Fetch a single asset by ``asset_id``. Raises AssetNotFoundError if absent."""
        try:
            result = await self.collection.find_one({"asset_id": asset_id})
        except PyMongoError as exc:
            raise DbError(f"Could not read asset {asset_id!r}") from exc

        if result is None:
            raise AssetNotFoundError(f"Asset {asset_id!r} not found")

        self.logger.debug("Fetched asset %r", asset_id)
        return Asset(**result)

    async def get_assets_by_project(
        self,
        project_id: str,
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> AsyncIterator[Asset]:
        """Yield all assets belonging to a project, with pagination and sorting."""
        try:
            cursor = (
                self.collection.find({"project_id": project_id})
                .sort(sort_by, 1 if sort_order == "asc" else -1)
                .skip((page - 1) * page_size)
                .limit(page_size)
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not read assets for project {project_id!r}"
            ) from exc

        self.logger.debug(
            "Fetching assets for project %r (page=%d, size=%d)", project_id, page, page_size
        )
        async for doc in cursor:
            yield Asset(**doc)

    async def iter_project_assets(self, project_id: str) -> AsyncIterator[Asset]:
        """Yield *every* asset in a project, oldest first, without pagination.

        get_assets_by_project() caps at ``page_size`` (10 by default), which
        silently drops everything past the tenth asset — acceptable for a
        listing endpoint, wrong for a caller that must process the whole
        project.
        """
        # find() builds a cursor synchronously — do NOT await it.
        cursor = self.collection.find({"project_id": project_id}).sort("created_at", 1)

        try:
            async for doc in cursor:
                yield Asset(**doc)
        except PyMongoError as exc:
            raise DbError(
                f"Could not read assets for project {project_id!r}"
            ) from exc

        self.logger.debug("Fetched every asset for project %r", project_id)

    async def iter_assets_for_projects(self, project_ids: list[str]) -> AsyncIterator[Asset]:
        """Every asset across several projects, newest first.

        One query rather than one per project: the artifacts panel lists a
        whole user's uploads, and a user with twenty chats would otherwise cost
        twenty round trips. file_bytes is projected out — the panel shows names
        and sizes, and the blobs would be megabytes of nothing useful.
        """
        if not project_ids:
            return

        cursor = self.collection.find(
            {"project_id": {"$in": project_ids}},
            projection={"file_bytes": 0},
        ).sort("created_at", -1)

        try:
            async for doc in cursor:
                yield Asset(**doc)
        except PyMongoError as exc:
            raise DbError("Could not read assets for the given projects") from exc

    async def find_by_content_hash(self, project_id: str, content_hash: str) -> Asset | None:
        """The project's asset with these exact bytes, or None.

        file_bytes is projected away: this runs on every upload and only needs
        to identify the existing document, not re-read it.
        """
        if not content_hash:
            return None

        try:
            record = await self.collection.find_one(
                {"project_id": project_id, "content_hash": content_hash},
                projection={"file_bytes": 0},
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not look up asset by content hash in project {project_id!r}"
            ) from exc

        return Asset(**record) if record else None

    async def delete_asset(self, asset_id: str) -> bool:
        """Remove one asset. The caller clears its chunks and vectors."""
        try:
            result = await self.collection.delete_one({"asset_id": asset_id})
        except PyMongoError as exc:
            raise DbError(f"Could not delete asset {asset_id!r}") from exc

        if result.deleted_count:
            self.logger.info("Deleted asset %r", asset_id)

        return result.deleted_count > 0

    async def delete_assets_for_project(self, project_id: str) -> None:
        """Drop every asset belonging to a project."""
        try:
            result = await self.collection.delete_many({"project_id": project_id})
        except PyMongoError as exc:
            raise DbError(f"Could not delete assets for project {project_id!r}") from exc

        self.logger.info(
            "Deleted %d asset(s) for project %r", result.deleted_count, project_id
        )
