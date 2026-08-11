from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import NotFoundError, StorageError
from .BaseModel import BaseModel
from .db_schema.asset import Asset, utcnow
from exceptions import AssetNotFoundError



class AssetModel(BaseModel):
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
            raise StorageError(
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
        from a failure. Failure is signalled by StorageError, nothing else.
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
            raise StorageError(
                f"Could not update asset {asset.asset_id!r}"
            ) from exc

        object_id = document["_id"]
        self.logger.info("Saved asset %r (_id=%s)", asset.asset_id, object_id)
        return object_id

    async def delete_asset(self, asset_id: str) -> bool:
        """Delete an asset by its ``asset_id``. Returns True if it existed."""
        try:
            result = await self.collection.delete_one({"asset_id": asset_id})
        except PyMongoError as exc:
            raise StorageError(
                f"Could not delete asset {asset_id!r}"
            ) from exc

        deleted = result.deleted_count > 0
        if deleted:
            self.logger.info("Deleted asset %r", asset_id)
        else:
            self.logger.warning("Delete requested for unknown asset %r", asset_id)
        return deleted

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_asset(self, asset_id: str) -> Asset:
        """Fetch a single asset by ``asset_id``. Raises AssetNotFoundError if absent."""
        try:
            result = await self.collection.find_one({"asset_id": asset_id})
        except PyMongoError as exc:
            raise StorageError(f"Could not read asset {asset_id!r}") from exc

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
            raise StorageError(
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
            raise StorageError(
                f"Could not read assets for project {project_id!r}"
            ) from exc

        self.logger.debug("Fetched every asset for project %r", project_id)

    async def get_assets_by_type(
        self,
        project_id: str,
        asset_type: str,
    ) -> AsyncIterator[Asset]:
        """Yield all assets of a given type within a project."""
        cursor = self.collection.find(
            {"project_id": project_id, "asset_type": asset_type}
        )
        try:
            async for doc in cursor:
                yield Asset(**doc)
        except PyMongoError as exc:
            raise StorageError(
                f"Could not read assets of type {asset_type!r} for project {project_id!r}"
            ) from exc

        self.logger.debug(
            "Fetched assets by type=%r for project %r", asset_type, project_id
        )

    # ------------------------------------------------------------------
    # Index helpers (inherited pattern)
    # ------------------------------------------------------------------

    def get_index(
        self,
        keys: list[tuple[str, int]],  # e.g. [("asset_id", 1), ("created_at", -1)]
        unique: bool = False,
        name: str | None = None,
    ) -> dict:
        """Build an index spec dict for create_index.

        keys: list of (field, direction) tuples — 1=ASC, -1=DESC
        """
        auto_name = "_".join(
            f"{field}_{'asc' if direction == 1 else 'desc'}" for field, direction in keys
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
        return await self.collection.create_index(
            index["key"], name=index["name"], unique=index["unique"]
        )
