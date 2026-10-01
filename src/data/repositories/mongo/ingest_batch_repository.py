from typing import AsyncIterator

from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from data.models import IngestBatch, IngestRun
from data.models.ingest import IngestBatchStatus
from data.models.project import utcnow
from shared.enums import DatabaseCollection
from shared.exceptions import DbError

from ..interfaces.ingest_batch_repository import IngestBatchRepository
from .base_model import BaseModel


class MongoIngestBatchRepository(IngestBatchRepository, BaseModel):
    """Data access for the ingest_batches and ingest_runs collections.

    Two collections behind one repository: they are written and deleted
    together and are meaningless apart, so splitting them would mean two
    repositories that may never be used independently.
    """

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.INGEST_BATCHES)
        self.runs = db[DatabaseCollection.INGEST_RUNS.value]

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def start_run(self, run: IngestRun) -> None:
        document = run.model_dump(by_alias=True)
        identity = {"_id": document.pop("_id"), "created_at": document.pop("created_at")}

        # Upsert, and collected_at goes back to None with it: re-ingesting a
        # document whose previous attempt died halfway must be collectable
        # again, and a stale claim would leave it never collecting.
        try:
            await self.runs.find_one_and_update(
                {"asset_id": run.asset_id},
                {"$set": document, "$setOnInsert": identity},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise DbError(f"Could not start ingest run for {run.asset_id!r}") from exc

    async def find_run(self, asset_id: str) -> IngestRun | None:
        try:
            record = await self.runs.find_one({"asset_id": asset_id})
        except PyMongoError as exc:
            raise DbError(f"Could not look up ingest run {asset_id!r}") from exc

        return IngestRun(**record) if record else None

    async def claim_collection(self, asset_id: str) -> bool:
        """One conditional update, and whoever matches is the collector.

        `find_one_and_update` with `collected_at: None` in the filter is atomic
        on a single document, so of however many batches finish together
        exactly one can move it off None.

        The count is read first here rather than folded into the statement as
        it is on Postgres, because Mongo cannot compare a field to a subquery.
        That is a wider window, but the claim itself still admits one winner:
        the worst case is a second caller reading the same count and then
        failing the filter, which is the intended outcome anyway.
        """
        try:
            corrected = await self.count_corrected(asset_id)

            claimed = await self.runs.find_one_and_update(
                {
                    "asset_id": asset_id,
                    "collected_at": None,
                    "total_batches": corrected,
                },
                {"$set": {"collected_at": utcnow()}},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise DbError(f"Could not claim collection for {asset_id!r}") from exc

        return claimed is not None

    # ------------------------------------------------------------------
    # Batches
    # ------------------------------------------------------------------

    async def save_batch(self, batch: IngestBatch) -> None:
        document = batch.model_dump(by_alias=True)
        identity = {"_id": document.pop("_id"), "created_at": document.pop("created_at")}
        document["updated_at"] = utcnow()

        try:
            await self.collection.find_one_and_update(
                {"asset_id": batch.asset_id, "batch_index": batch.batch_index},
                {"$set": document, "$setOnInsert": identity},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise DbError(f"Could not save batch {batch.batch_index} of {batch.asset_id!r}") from exc

    async def find_batch(self, asset_id: str, batch_index: int) -> IngestBatch | None:
        try:
            record = await self.collection.find_one({"asset_id": asset_id, "batch_index": batch_index})
        except PyMongoError as exc:
            raise DbError(f"Could not look up batch {batch_index} of {asset_id!r}") from exc

        return IngestBatch(**record) if record else None

    async def count_corrected(self, asset_id: str) -> int:
        try:
            return await self.collection.count_documents({"asset_id": asset_id, "status": IngestBatchStatus.CORRECTED})
        except PyMongoError as exc:
            raise DbError(f"Could not count batches for {asset_id!r}") from exc

    async def iter_batches(self, asset_id: str) -> AsyncIterator[IngestBatch]:
        try:
            cursor = self.collection.find({"asset_id": asset_id}).sort("batch_index", 1)

            async for record in cursor:
                yield IngestBatch(**record)

        except PyMongoError as exc:
            raise DbError(f"Could not list batches for {asset_id!r}") from exc

    async def clear_run(self, asset_id: str) -> None:
        try:
            await self.collection.delete_many({"asset_id": asset_id})
            await self.runs.delete_one({"asset_id": asset_id})
        except PyMongoError as exc:
            raise DbError(f"Could not clear ingest run {asset_id!r}") from exc
