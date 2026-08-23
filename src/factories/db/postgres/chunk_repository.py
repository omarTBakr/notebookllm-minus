import json
from typing import AsyncIterator

from exceptions import DbError
from models.db_schema import DataChunk
from .base_repository import PostgresBaseRepository
from ..interfaces.chunk_repository import ChunkRepository


class PostgresChunkRepository(PostgresBaseRepository, ChunkRepository):
    """PostgreSQL implementation of ChunkRepository."""

    async def create_chunks(self, chunks: list[DataChunk]) -> list[str]:
        if not chunks:
            return []
            
        inserted_ids = []
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    for chunk in chunks:
                        record_id = self._generate_id()
                        await conn.execute(
                            """
                            INSERT INTO chunks (id, project_id, asset_id, chunk_order, chunk_content, chunk_metadata, created_at, updated_at)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            """,
                            record_id,
                            str(chunk.project_id),
                            chunk.asset_id,
                            chunk.chunk_order,
                            chunk.chunk_content,
                            json.dumps(chunk.chunk_metadata),
                            chunk.created_at,
                            chunk.updated_at,
                        )
                        inserted_ids.append(record_id)
            return inserted_ids
        except Exception as exc:
            raise DbError(f"Failed to create chunks: {exc}") from exc

    async def iter_chunks(self, project_object_id: str) -> AsyncIterator[DataChunk]:
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    query = "SELECT * FROM chunks WHERE project_id = $1 ORDER BY chunk_order ASC"
                    async for record in conn.cursor(query, project_object_id):
                        yield self._record_to_model(record, DataChunk)
        except Exception as exc:
            raise DbError(f"Failed to iterate chunks: {exc}") from exc

    async def iter_project_chunks(
        self, project_id: str, asset_id: str | None = None
    ) -> AsyncIterator[DataChunk]:
        """Every chunk of a project, or of one asset within it.

        The asset filter is what re-indexing a single upload uses; without it
        this returned the whole project and re-embedded everything.
        """
        query = "SELECT * FROM chunks WHERE project_id = $1"
        args: list = [str(project_id)]

        if asset_id is not None:
            query += " AND asset_id = $2"
            args.append(asset_id)

        query += " ORDER BY chunk_order ASC"

        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    async for record in conn.cursor(query, *args):
                        yield self._record_to_model(record, DataChunk)
        except Exception as exc:
            raise DbError(f"Failed to iterate project chunks: {exc}") from exc
        
    async def count_project_chunks(self, project_id: str, asset_id: str | None = None) -> int:
        try:
            async with self.pool.acquire() as conn:
                if asset_id:
                    return await conn.fetchval(
                        "SELECT COUNT(*) FROM chunks WHERE project_id = $1 AND asset_id = $2",
                        str(project_id), asset_id
                    )
                else:
                    return await conn.fetchval(
                        "SELECT COUNT(*) FROM chunks WHERE project_id = $1",
                        str(project_id)
                    )
        except Exception as exc:
            raise DbError(f"Failed to count chunks: {exc}") from exc

    async def has_asset_chunks(self, project_id: str, asset_id: str) -> bool:
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    "SELECT 1 FROM chunks WHERE project_id = $1 AND asset_id = $2 LIMIT 1",
                    str(project_id), asset_id
                )
                return bool(result)
        except Exception as exc:
            raise DbError(f"Failed to check asset chunks: {exc}") from exc

    async def delete_chunks_for_project(self, project_id: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("DELETE FROM chunks WHERE project_id = $1", str(project_id))
        except Exception as exc:
            raise DbError(f"Failed to delete chunks for project: {exc}") from exc

    async def delete_chunks_for_asset(self, project_id: str, asset_id: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM chunks WHERE project_id = $1 AND asset_id = $2",
                    str(project_id), asset_id
                )
        except Exception as exc:
            raise DbError(f"Failed to delete chunks for asset: {exc}") from exc
