from typing import AsyncIterator, Iterable, Sequence

from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from pymongo import ASCENDING
from pymongo.errors import PyMongoError

from enums import DatabaseCollection
from exceptions import StorageError
from .BaseModel import BaseModel
from .db_schema import DataChunk

# Documents per insert_many call. Large enough to amortise round trips, small
# enough to stay well under MongoDB's 16 MB / 100k-document write limits even
# when chunk_content is sizeable.
DEFAULT_BATCH_SIZE = 100


class ChunkModel(BaseModel):
    """Data access for the data_chunks collection.

    Note on identifiers: ``DataChunk.project_id`` is the project's Mongo
    ``_id`` (an ObjectId), not the string ``Project.project_id`` that appears
    in URLs. Resolve the string to a project first, then pass ``project.id``.

    Raises typed errors and lets them propagate; deciding what a failure means
    for the caller is not this layer's job.
    """

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.DATA_CHUNKS)

    async def ensure_indexes(self) -> None:
        """Create the indexes the read and delete paths rely on.

        Not called automatically — run it once at startup, not per request.
        """
        try:
            await self.collection.create_index(
                [("project_id", ASCENDING), ("chunk_order", ASCENDING)],
                name="project_id_chunk_order",
            )
        except PyMongoError as exc:
            raise StorageError("Could not create chunk indexes") from exc

        self.logger.info("Ensured indexes on %s", DatabaseCollection.DATA_CHUNKS.value)

    async def create_chunk(self, chunk: DataChunk) -> ObjectId:
        """Insert a single chunk and return its _id."""
        try:
            # by_alias writes the model's id as `_id`, so the document has one
            # identifier rather than an `id` field plus a generated `_id`.
            result = await self.collection.insert_one(chunk.model_dump(by_alias=True))
        except PyMongoError as exc:
            raise StorageError(
                f"Could not create chunk {chunk.chunk_order} "
                f"for project {chunk.project_id}"
            ) from exc

        self.logger.debug(
            "Created chunk %s for project %s (_id=%s)",
            chunk.chunk_order,
            chunk.project_id,
            result.inserted_id,
        )
        return result.inserted_id

    async def create_chunks(
        self,
        chunks: Sequence[DataChunk],
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> list[ObjectId]:
        """Insert many chunks in batches; returns the list of inserted _ids.

        Batches are unordered so one rejected document doesn't abandon the rest
        of its batch. A failure part-way through still leaves earlier batches
        written — callers that need all-or-nothing should follow a StorageError
        with ``delete_chunks_by_project`` before retrying.
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
                raise StorageError(
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
            raise StorageError(f"Could not read chunks for project {project_id}") from exc

    async def count_project_chunks(self, project_id: ObjectId) -> int:
        try:
            return await self.collection.count_documents({"project_id": project_id})
        except PyMongoError as exc:
            raise StorageError(
                f"Could not count chunks for project {project_id}"
            ) from exc

    async def delete_project_chunks(self, project_id: ObjectId) -> int:
        """Remove every chunk of a project; returns how many were deleted.

        This is what the process endpoint's `reset` flag needs in order to
        re-ingest a document without leaving the previous chunks behind.
        """
        try:
            result = await self.collection.delete_many({"project_id": project_id})
        except PyMongoError as exc:
            raise StorageError(
                f"Could not delete chunks for project {project_id}"
            ) from exc

        self.logger.info(
            "Deleted %d chunk(s) for project %s", result.deleted_count, project_id
        )
        return result.deleted_count

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



def _batched(items: Sequence[DataChunk], size: int) -> Iterable[Sequence[DataChunk]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
