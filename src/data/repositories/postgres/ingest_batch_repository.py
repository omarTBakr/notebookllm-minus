from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from data.models import IngestBatch, IngestRun
from data.models.ingest import IngestBatchStatus
from shared.exceptions import DbError

from ..interfaces.ingest_batch_repository import IngestBatchRepository
from .base_repository import IngestBatchRow, IngestRunRow, PostgresBaseRepository


class PostgresIngestBatchRepository(PostgresBaseRepository, IngestBatchRepository):
    """PostgreSQL implementation of IngestBatchRepository."""

    # --- runs ------------------------------------------------------------------

    async def start_run(self, run: IngestRun) -> None:
        statement = insert(IngestRunRow).values(
            id=self._generate_id(),
            asset_id=run.asset_id,
            project_id=run.project_id,
            total_batches=run.total_batches,
            request_data=self._scrub(run.request_data),
            project_object_id=run.project_object_id,
            parent_task_id=run.parent_task_id,
            index_task_id=run.index_task_id,
            build_task_id=run.build_task_id,
            collected_at=None,
            created_at=run.created_at,
        )
        # Upsert, and note collected_at is reset to NULL: re-ingesting a
        # document whose previous attempt died halfway must be collectable
        # again, and a stale claim left behind would make it never collect.
        statement = statement.on_conflict_do_update(
            index_elements=["asset_id"],
            set_={
                "project_id": statement.excluded.project_id,
                "total_batches": statement.excluded.total_batches,
                "request_data": statement.excluded.request_data,
                "project_object_id": statement.excluded.project_object_id,
                "parent_task_id": statement.excluded.parent_task_id,
                "index_task_id": statement.excluded.index_task_id,
                "build_task_id": statement.excluded.build_task_id,
                "collected_at": None,
            },
        )

        try:
            async with self.session_factory.begin() as db:
                await db.execute(statement)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to start ingest run for {run.asset_id!r}: {exc}") from exc

    async def find_run(self, asset_id: str) -> IngestRun | None:
        try:
            async with self.session_factory() as db:
                result = await db.execute(select(IngestRunRow).where(IngestRunRow.asset_id == asset_id))
                row = result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to look up ingest run {asset_id!r}: {exc}") from exc

        if row is None:
            return None

        return self._record_to_model(row.__dict__, IngestRun)

    async def claim_collection(self, asset_id: str) -> bool:
        """One UPDATE, and whoever gets a row back is the collector.

        The whole replacement for the chord is in this statement. Both
        conditions matter and neither can be split out into a separate read
        without reopening the race it closes:

        *collected_at IS NULL* is what makes it single-winner. Two batches
        finishing in the same instant both run this; Postgres serialises the
        row lock, the first sets the timestamp, the second matches nothing.

        *the subquery* is what stops it firing early. Counting in the same
        statement means the count and the claim see one snapshot, where a
        count-then-claim could be told "23 of 23" and then claim after a
        retried batch had reset one to `parsed`.
        """
        counted = (
            select(func.count())
            .select_from(IngestBatchRow)
            .where(
                IngestBatchRow.asset_id == asset_id,
                IngestBatchRow.status == IngestBatchStatus.CORRECTED,
            )
            .scalar_subquery()
        )

        statement = (
            update(IngestRunRow)
            .where(
                IngestRunRow.asset_id == asset_id,
                IngestRunRow.collected_at.is_(None),
                IngestRunRow.total_batches == counted,
            )
            .values(collected_at=datetime.now(timezone.utc))
            .returning(IngestRunRow.asset_id)
        )

        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(statement)
                return result.scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to claim collection for {asset_id!r}: {exc}") from exc

    # --- batches ---------------------------------------------------------------

    async def save_batch(self, batch: IngestBatch) -> None:
        statement = insert(IngestBatchRow).values(
            id=self._generate_id(),
            asset_id=batch.asset_id,
            project_id=batch.project_id,
            batch_index=batch.batch_index,
            status=batch.status,
            asset_name=batch.asset_name,
            # _scrub, and not optionally: this payload is PDF text, which is
            # exactly where NUL bytes come from, and Postgres refuses \x00 in
            # both text and jsonb. One of them would fail the whole insert.
            payload=self._scrub(batch.payload),
            created_at=batch.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        statement = statement.on_conflict_do_update(
            index_elements=["asset_id", "batch_index"],
            set_={
                "status": statement.excluded.status,
                "asset_name": statement.excluded.asset_name,
                "payload": statement.excluded.payload,
                "updated_at": statement.excluded.updated_at,
            },
        )

        try:
            async with self.session_factory.begin() as db:
                await db.execute(statement)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to save batch {batch.batch_index} of {batch.asset_id!r}: {exc}") from exc

    async def find_batch(self, asset_id: str, batch_index: int) -> IngestBatch | None:
        try:
            async with self.session_factory() as db:
                result = await db.execute(
                    select(IngestBatchRow).where(
                        IngestBatchRow.asset_id == asset_id,
                        IngestBatchRow.batch_index == batch_index,
                    )
                )
                row = result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to look up batch {batch_index} of {asset_id!r}: {exc}") from exc

        if row is None:
            return None

        return self._record_to_model(row.__dict__, IngestBatch)

    async def count_corrected(self, asset_id: str) -> int:
        try:
            async with self.session_factory() as db:
                result = await db.execute(
                    select(func.count())
                    .select_from(IngestBatchRow)
                    .where(
                        IngestBatchRow.asset_id == asset_id,
                        IngestBatchRow.status == IngestBatchStatus.CORRECTED,
                    )
                )
                return int(result.scalar_one() or 0)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to count batches for {asset_id!r}: {exc}") from exc

    async def iter_batches(self, asset_id: str) -> AsyncIterator[IngestBatch]:
        try:
            async with self.session_factory() as db:
                stream = await db.stream_scalars(
                    select(IngestBatchRow)
                    .where(IngestBatchRow.asset_id == asset_id)
                    .order_by(IngestBatchRow.batch_index)
                )
                async for row in stream:
                    yield self._record_to_model(row.__dict__, IngestBatch)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to list batches for {asset_id!r}: {exc}") from exc

    async def clear_run(self, asset_id: str) -> None:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(delete(IngestBatchRow).where(IngestBatchRow.asset_id == asset_id))
                await db.execute(delete(IngestRunRow).where(IngestRunRow.asset_id == asset_id))
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to clear ingest run {asset_id!r}: {exc}") from exc
