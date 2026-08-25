from typing import AsyncIterator

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from exceptions import AssetNotFoundError, DbError
from models.db_schema import Asset
from .base_repository import AssetRow, PostgresBaseRepository
from ..interfaces.asset_repository import AssetRepository


class PostgresAssetRepository(PostgresBaseRepository, AssetRepository):
    """PostgreSQL implementation of AssetRepository."""

    async def create_asset(self, asset: Asset) -> str:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    insert(AssetRow).values(
                        id=self._generate_id(),
                        asset_id=asset.asset_id,
                        project_id=asset.project_id,
                        asset_type=asset.asset_type.value,
                        name=asset.name,
                        description=asset.description,
                        file_bytes=asset.file_bytes,
                        content_hash=asset.content_hash,
                        created_at=asset.created_at,
                        updated_at=asset.updated_at,
                    )
                )
            return asset.asset_id
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create asset: {exc}") from exc

    async def update_asset(self, asset: Asset) -> ObjectId:
        """Create the asset or overwrite it, and return its row id.

        An upsert, matching Mongo: the ingest routes call this to record a
        freshly uploaded file, so the first write must insert rather than
        raise "not found". Returns the row's ObjectId, which is what the
        project's assets_ids list holds.
        """
        statement = insert(AssetRow).values(
            id=self._generate_id(),
            asset_id=asset.asset_id,
            project_id=asset.project_id,
            asset_type=asset.asset_type.value,
            name=asset.name,
            description=asset.description,
            file_bytes=asset.file_bytes,
            content_hash=asset.content_hash,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["asset_id"],
            set_={
                "name": statement.excluded.name,
                "description": statement.excluded.description,
                "file_bytes": statement.excluded.file_bytes,
                "updated_at": statement.excluded.updated_at,
            },
        ).returning(AssetRow.id)

        try:
            async with self.session_factory.begin() as db:
                row_id = await db.scalar(statement)
            return ObjectId(row_id)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to update asset: {exc}") from exc

    async def get_asset(self, asset_id: str) -> Asset:
        try:
            async with self.session_factory() as db:
                row = await db.scalar(
                    select(AssetRow).where(AssetRow.asset_id == asset_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to get asset: {exc}") from exc

        if row is None:
            raise AssetNotFoundError(f"Asset {asset_id!r} not found")

        return self._record_to_model(row, Asset)

    async def iter_assets_for_projects(self, project_ids: list[str]) -> AsyncIterator[Asset]:
        if not project_ids:
            return

        try:
            async with self.session_factory() as db:
                result = await db.stream_scalars(
                    select(AssetRow)
                    .where(AssetRow.project_id.in_(project_ids))
                    .order_by(AssetRow.created_at.desc())
                )
                async for row in result:
                    yield self._record_to_model(row, Asset)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to iterate assets for projects: {exc}") from exc

    async def find_by_content_hash(self, project_id: str, content_hash: str) -> Asset | None:
        """The project's asset with these exact bytes, or None.

        Everything but file_bytes: this runs on every upload, and loading a
        50 MB column to answer "have I seen this?" would cost more than the
        duplicate it prevents. Served by uq_assets_project_content.
        """
        if not content_hash:
            return None

        columns = [c for c in AssetRow.__table__.c if c.name != "file_bytes"]

        try:
            async with self.session_factory() as db:
                result = await db.execute(
                    select(*columns).where(
                        AssetRow.project_id == project_id,
                        AssetRow.content_hash == content_hash,
                    )
                )
                row = result.mappings().first()
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to look up asset by content hash: {exc}") from exc

        # file_bytes is absent by design; the model defaults it to b"".
        return self._record_to_model(row, Asset) if row else None

    async def rename(self, asset_id: str, name: str) -> None:
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    update(AssetRow)
                    .where(AssetRow.asset_id == asset_id)
                    .values(name=name, updated_at=func.now())
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to rename asset: {exc}") from exc

        if result.rowcount == 0:
            raise AssetNotFoundError(f"Asset {asset_id!r} not found")

    async def delete_asset(self, asset_id: str) -> bool:
        """Remove one asset row. The caller clears its chunks and vectors."""
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    delete(AssetRow).where(AssetRow.asset_id == asset_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete asset: {exc}") from exc

        return result.rowcount > 0

    async def delete_assets_for_project(self, project_id: str) -> None:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    delete(AssetRow).where(AssetRow.project_id == project_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete assets for project: {exc}") from exc
