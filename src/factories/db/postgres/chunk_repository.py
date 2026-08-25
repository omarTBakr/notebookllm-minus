from typing import AsyncIterator

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError

from exceptions import DbError
from models.db_schema import DataChunk
from .base_repository import ChunkRow, PostgresBaseRepository
from ..interfaces.chunk_repository import ChunkRepository


class PostgresChunkRepository(PostgresBaseRepository, ChunkRepository):
    """PostgreSQL implementation of ChunkRepository."""

    async def create_chunks(self, chunks: list[DataChunk]) -> list[str]:
        if not chunks:
            return []

        rows = []
        inserted_ids = []

        for chunk in chunks:
            record_id = self._generate_id()
            inserted_ids.append(record_id)
            rows.append(
                {
                    "id": record_id,
                    # DataChunk.project_id is the project's row ObjectId.
                    "project_id": str(chunk.project_id),
                    "asset_id": chunk.asset_id,
                    "chunk_order": chunk.chunk_order,
                    # Scrubbed here as well as at extraction: this INSERT is
                    # batched, so one NUL anywhere fails every row with it.
                    "chunk_content": self._scrub(chunk.chunk_content),
                    "chunk_metadata": self._scrub(chunk.chunk_metadata),
                    "created_at": chunk.created_at,
                    "updated_at": chunk.updated_at,
                }
            )

        try:
            # One executemany rather than a row per await. Ingest writes every
            # chunk of a document at once, so this is the hot path.
            async with self.session_factory.begin() as db:
                await db.execute(insert(ChunkRow), rows)
            return inserted_ids
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create chunks: {exc}") from exc

    def _select_chunks(self, project_id: str, asset_id: str | None = None):
        statement = select(ChunkRow).where(ChunkRow.project_id == str(project_id))

        if asset_id is not None:
            statement = statement.where(ChunkRow.asset_id == asset_id)

        return statement.order_by(ChunkRow.chunk_order.asc())

    async def iter_chunks(self, project_object_id: str) -> AsyncIterator[DataChunk]:
        try:
            async with self.session_factory() as db:
                result = await db.stream_scalars(self._select_chunks(project_object_id))
                async for row in result:
                    yield self._record_to_model(row, DataChunk)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to iterate chunks: {exc}") from exc

    async def iter_project_chunks(
        self, project_id: str, asset_id: str | None = None
    ) -> AsyncIterator[DataChunk]:
        """Every chunk of a project, or of one asset within it.

        The asset filter is what re-indexing a single upload uses; without it
        this returned the whole project and re-embedded everything.
        """
        try:
            async with self.session_factory() as db:
                result = await db.stream_scalars(
                    self._select_chunks(project_id, asset_id)
                )
                async for row in result:
                    yield self._record_to_model(row, DataChunk)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to iterate project chunks: {exc}") from exc

    async def count_project_chunks(self, project_id: str, asset_id: str | None = None) -> int:
        statement = (
            select(func.count())
            .select_from(ChunkRow)
            .where(ChunkRow.project_id == str(project_id))
        )

        if asset_id:
            statement = statement.where(ChunkRow.asset_id == asset_id)

        try:
            async with self.session_factory() as db:
                return await db.scalar(statement)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to count chunks: {exc}") from exc

    async def has_asset_chunks(self, project_id: str, asset_id: str) -> bool:
        try:
            async with self.session_factory() as db:
                found = await db.scalar(
                    select(ChunkRow.id)
                    .where(
                        ChunkRow.project_id == str(project_id),
                        ChunkRow.asset_id == asset_id,
                    )
                    .limit(1)
                )
                return found is not None
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to check asset chunks: {exc}") from exc

    async def delete_chunks_for_project(self, project_id: str) -> None:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    delete(ChunkRow).where(ChunkRow.project_id == str(project_id))
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete chunks for project: {exc}") from exc

    async def delete_chunks_for_asset(self, project_id: str, asset_id: str) -> list[str]:
        """Delete one asset's chunks; returns the row ids that were removed.

        RETURNING rather than a bare DELETE so this matches Mongo, which hands
        the ids back for the caller to pull out of the project's chunks_ids.
        routes/process.py takes len() of the result, so returning None here
        was a TypeError waiting for the first reset=true on this backend.
        """
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    delete(ChunkRow)
                    .where(
                        ChunkRow.project_id == str(project_id),
                        ChunkRow.asset_id == asset_id,
                    )
                    .returning(ChunkRow.id)
                )
                return [row[0] for row in result.all()]
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete chunks for asset: {exc}") from exc
