from typing import AsyncIterator, Iterable, Sequence

from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from pymongo import ASCENDING  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import DatabaseCollection
from exceptions import DbError
from ..interfaces.chunk_repository import ChunkRepository
from .base_model import BaseModel
from models.db_schema import DataChunk

# Documents per insert_many call. Large enough to amortise round trips, small
# enough to stay well under MongoDB's 16 MB / 100k-document write limits even
# when chunk_content is sizeable.
DEFAULT_BATCH_SIZE = 100


class MongoChunkRepository(ChunkRepository, BaseModel):
    """Data access for the data_chunks collection.

    Note on identifiers: ``DataChunk.project_id`` is the project's Mongo
    ``_id`` (an ObjectId), not the string ``Project.project_id`` that appears
    in URLs. Resolve the string to a project first, then pass ``project.id``.

    Raises typed errors and lets them propagate; deciding what a failure means
    for the caller is not this layer's job.
    """

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.DATA_CHUNKS)

    async def create_chunks(
        self,
        chunks: Sequence[DataChunk],
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> list[ObjectId]:
        """Insert many chunks in batches; returns the list of inserted _ids.

        Batches are unordered so one rejected document doesn't abandon the rest
        of its batch. A failure part-way through still leaves earlier batches
        written — callers that need all-or-nothing should follow a DbError
        with ``delete_project_chunks`` before retrying.
        """
        if not chunks:
            self.logger.debug("create_chunks called with nothing to insert")
            return []

        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")

        all_ids: list[ObjectId] = []
        for batch in _batched(chunks, batch_size):
            documents = [chunk.model_dump(by_alias=True) for chunk in batch]
            try:
                result = await self.collection.insert_many(documents, ordered=False)
            except PyMongoError as exc:
                raise DbError(
                    f"Could not insert chunks: {len(all_ids)} of {len(chunks)} "
                    f"were written before the failure"
                ) from exc
            all_ids.extend(result.inserted_ids)

        self.logger.info(
            "Inserted %d chunk(s) for project %s",
            len(all_ids),
            chunks[0].project_id,
        )
        return all_ids

    async def get_project_chunks(
        self, project_id: ObjectId, page: int = 1, page_size: int = 100
    ) -> AsyncIterator[DataChunk]:
        """Yield a project's chunks in chunk_order, one page at a time."""
        # find() builds a cursor synchronously — do NOT await it.
        cursor = (
            self.collection.find({"project_id": project_id})
            .sort("chunk_order", ASCENDING)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )

        try:
            async for document in cursor:
                yield DataChunk(**document)
        except PyMongoError as exc:
            raise DbError(f"Could not read chunks for project {project_id}") from exc

    async def iter_project_chunks(
        self, project_id: ObjectId, asset_id: str | None = None
    ) -> AsyncIterator[DataChunk]:
        """Yield *every* chunk of a project, or of one asset within it.

        Unpaginated on purpose, and the counterpart to
        ``AssetModel.iter_project_assets``: ``get_project_chunks`` returns a
        single page, so indexing a project through it would silently stop at
        ``page_size``. A single cursor also avoids ``.skip()``, which grows
        slower per page and can drop or repeat rows if writes land mid-run.

        Sorted by ``(asset_id, chunk_order)`` — ``chunk_order`` restarts at 0
        for each document, so ordering on it alone interleaves two sources.
        """
        query: dict = {"project_id": project_id}
        if asset_id is not None:
            query["asset_id"] = asset_id

        # find() builds a cursor synchronously — do NOT await it.
        cursor = self.collection.find(query).sort(
            [("asset_id", ASCENDING), ("chunk_order", ASCENDING)]
        )

        try:
            async for document in cursor:
                yield DataChunk(**document)
        except PyMongoError as exc:
            raise DbError(
                f"Could not read chunks for project {project_id}"
            ) from exc

        self.logger.debug(
            "Fetched every chunk for project %s (asset_id=%r)", project_id, asset_id
        )

    async def count_project_chunks(
        self, project_id: ObjectId, asset_id: str | None = None
    ) -> int:
        """Chunks in a project, or in one asset of it.

        The optional narrowing exists so callers that act on a single asset can
        count the same scope they are about to act on.
        """
        query: dict = {"project_id": project_id}
        if asset_id is not None:
            query["asset_id"] = asset_id

        try:
            return await self.collection.count_documents(query)
        except PyMongoError as exc:
            raise DbError(
                f"Could not count chunks for project {project_id}"
            ) from exc

    async def has_asset_chunks(self, project_id: ObjectId, asset_id: str) -> bool:
        """True if this asset has already been chunked into this project.

        The existence check the process route uses to stay idempotent: an asset
        that already has chunks is skipped rather than inserted a second time.
        """
        try:
            found = await self.collection.find_one(
                {"project_id": project_id, "asset_id": asset_id},
                projection={"_id": 1},
            )
        except PyMongoError as exc:
            raise DbError(
                f"Could not check existing chunks for asset {asset_id!r}"
            ) from exc

        return found is not None

    async def get_chunks_by_orders(
        self, asset_id: str, chunk_orders: list[int]
    ) -> dict[int, DataChunk]:
        """The named chunks of one asset, keyed by chunk_order.

        Turns a search hit back into a place in the document: the vector
        payload carries ``(asset_id, chunk_order)``, the page number and the
        highlight geometry live here.
        """
        if not chunk_orders:
            return {}

        try:
            cursor = self.collection.find(
                {
                    "asset_id": asset_id,
                    "chunk_order": {"$in": sorted(set(chunk_orders))},
                }
            )
            return {
                document["chunk_order"]: DataChunk(**document)
                async for document in cursor
            }
        except PyMongoError as exc:
            raise DbError(
                f"Could not read chunks by order for asset {asset_id!r}"
            ) from exc

    async def delete_chunks_for_asset(
        self, project_id: ObjectId, asset_id: str
    ) -> list[ObjectId]:
        """Delete one asset's chunks; returns the _ids that were removed.

        The ids come back so the caller can pull exactly those out of the
        project's ``chunks_ids``. ``delete_project_chunks`` + ``clear_chunk_ids``
        is the whole-project equivalent, and using it to re-ingest a single
        document would take every other asset's chunks down with it.
        """
        try:
            cursor = self.collection.find(
                {"project_id": project_id, "asset_id": asset_id},
                projection={"_id": 1},
            )
            removed_ids = [document["_id"] async for document in cursor]

            if removed_ids:
                await self.collection.delete_many({"_id": {"$in": removed_ids}})
        except PyMongoError as exc:
            raise DbError(
                f"Could not delete chunks for asset {asset_id!r}"
            ) from exc

        self.logger.info(
            "Deleted %d chunk(s) for asset %r in project %s",
            len(removed_ids),
            asset_id,
            project_id,
        )
        return removed_ids

    async def delete_chunks_for_project(self, project_id: ObjectId) -> int:
        """Remove every chunk of a project; returns how many were deleted.

        This is what the process endpoint's `reset` flag needs in order to
        re-ingest a document without leaving the previous chunks behind.
        """
        try:
            result = await self.collection.delete_many({"project_id": project_id})
        except PyMongoError as exc:
            raise DbError(
                f"Could not delete chunks for project {project_id}"
            ) from exc

        self.logger.info(
            "Deleted %d chunk(s) for project %s", result.deleted_count, project_id
        )
        return result.deleted_count


    async def iter_chunks(self, project_object_id: ObjectId) -> AsyncIterator[DataChunk]:
        """Every chunk under a project, in the order they were split."""
        try:
            cursor = self.collection.find({"project_id": project_object_id})
            async for document in cursor.sort("chunk_order", 1):
                yield DataChunk(**document)
        except PyMongoError as exc:
            raise DbError(
                f"Could not read chunks for project {project_object_id!r}"
            ) from exc

def _batched(items: Sequence[DataChunk], size: int) -> Iterable[Sequence[DataChunk]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
