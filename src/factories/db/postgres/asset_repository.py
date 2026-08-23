from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]

from exceptions import AssetNotFoundError, DbError
from models.db_schema import Asset
from .base_repository import PostgresBaseRepository
from ..interfaces.asset_repository import AssetRepository


class PostgresAssetRepository(PostgresBaseRepository, AssetRepository):
    """PostgreSQL implementation of AssetRepository."""

    async def create_asset(self, asset: Asset) -> str:
        record_id = self._generate_id()
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO assets (id, asset_id, project_id, asset_type, name, description, file_bytes, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    record_id,
                    asset.asset_id,
                    asset.project_id,
                    asset.asset_type.value,
                    asset.name,
                    asset.description,
                    asset.file_bytes,
                    asset.created_at,
                    asset.updated_at,
                )
            return asset.asset_id
        except Exception as exc:
            raise DbError(f"Failed to create asset: {exc}") from exc

    async def update_asset(self, asset: Asset) -> ObjectId:
        """Create the asset or overwrite it, and return its row id.

        An upsert, matching Mongo: the ingest routes call this to record a
        freshly uploaded file, so the first write must insert rather than
        raise "not found". Returns the row's ObjectId, which is what the
        project's assets_ids list holds.
        """
        record_id = self._generate_id()
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO assets (id, asset_id, project_id, asset_type,
                                        name, description, file_bytes,
                                        created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (asset_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        file_bytes = EXCLUDED.file_bytes,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id
                    """,
                    record_id,
                    asset.asset_id,
                    asset.project_id,
                    asset.asset_type.value,
                    asset.name,
                    asset.description,
                    asset.file_bytes,
                    asset.created_at,
                    asset.updated_at,
                )
            return ObjectId(row["id"])
        except Exception as exc:
            raise DbError(f"Failed to update asset: {exc}") from exc

    async def get_asset(self, asset_id: str) -> Asset:
        try:
            async with self.pool.acquire() as conn:
                record = await conn.fetchrow(
                    "SELECT * FROM assets WHERE asset_id = $1", asset_id
                )
        except Exception as exc:
            raise DbError(f"Failed to get asset: {exc}") from exc

        if not record:
            raise AssetNotFoundError(f"Asset {asset_id!r} not found")
        
        return self._record_to_model(record, Asset)

    async def iter_assets_for_projects(self, project_ids: list[str]) -> AsyncIterator[Asset]:
        if not project_ids:
            return
            
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    query = "SELECT * FROM assets WHERE project_id = ANY($1) ORDER BY created_at DESC"
                    async for record in conn.cursor(query, project_ids):
                        yield self._record_to_model(record, Asset)
        except Exception as exc:
            raise DbError(f"Failed to iterate assets for projects: {exc}") from exc

    async def rename(self, asset_id: str, name: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE assets SET name = $1, updated_at = CURRENT_TIMESTAMP WHERE asset_id = $2",
                    name,
                    asset_id,
                )
                if result == "UPDATE 0":
                    raise AssetNotFoundError(f"Asset {asset_id!r} not found")
        except AssetNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to rename asset: {exc}") from exc

    async def delete_assets_for_project(self, project_id: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("DELETE FROM assets WHERE project_id = $1", project_id)
        except Exception as exc:
            raise DbError(f"Failed to delete assets for project: {exc}") from exc
