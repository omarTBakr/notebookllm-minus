from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from exceptions import DbError
from models.db_schema import Artifact

from ..interfaces.artifact_repository import ArtifactRepository
from .base_repository import ArtifactRow, PostgresBaseRepository


class PostgresArtifactRepository(PostgresBaseRepository, ArtifactRepository):
    """PostgreSQL implementation of ArtifactRepository."""

    async def create_artifact(self, artifact: Artifact) -> str:
        # Upsert on (chat_id, kind) rather than insert: regenerating replaces
        # the current set, and the unique index makes a plain insert raise on
        # the second attempt. Everything is reset -- a regeneration that
        # inherited the previous run's items would show old cards alongside new
        # ones with no way to tell them apart.
        statement = (
            insert(ArtifactRow)
            .values(
                id=self._generate_id(),
                artifact_id=artifact.artifact_id,
                chat_id=artifact.chat_id,
                kind=artifact.kind.value,
                status=artifact.status.value,
                items=artifact.items,
                source_task_id=artifact.source_task_id,
                error=artifact.error,
            )
            .on_conflict_do_update(
                index_elements=[ArtifactRow.chat_id, ArtifactRow.kind],
                set_={
                    "artifact_id": artifact.artifact_id,
                    "status": artifact.status.value,
                    "items": artifact.items,
                    "source_task_id": artifact.source_task_id,
                    "error": artifact.error,
                    "updated_at": func.now(),
                },
            )
            .returning(ArtifactRow.artifact_id)
        )

        try:
            async with self.session_factory.begin() as db:
                return await db.scalar(statement)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create artifact: {exc}") from exc

    async def find_artifact(self, chat_id: str, kind: str) -> Artifact | None:
        statement = select(ArtifactRow).where(ArtifactRow.chat_id == chat_id, ArtifactRow.kind == kind)

        try:
            async with self.session_factory() as db:
                row = (await db.execute(statement)).scalar_one_or_none()

                return self._record_to_model(row, Artifact) if row else None
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to read artifact: {exc}") from exc

    async def append_items(self, artifact_id: str, items: list[dict]) -> int:
        if not items:
            return await self._count_items(artifact_id)

        # Concatenation in the database, not a read-modify-write here. This runs
        # once per batch while the browser may already be reading the set, so a
        # worker that read, appended and wrote back would lose items to any
        # concurrent write and would briefly serve a shorter deck than the one
        # already on screen.
        statement = (
            update(ArtifactRow)
            .where(ArtifactRow.artifact_id == artifact_id)
            .values(items=ArtifactRow.items + items, updated_at=func.now())
            .returning(func.jsonb_array_length(ArtifactRow.items))
        )

        try:
            async with self.session_factory.begin() as db:
                total = await db.scalar(statement)

                return total or 0
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to append artifact items: {exc}") from exc

    async def _count_items(self, artifact_id: str) -> int:
        statement = select(func.jsonb_array_length(ArtifactRow.items)).where(ArtifactRow.artifact_id == artifact_id)

        try:
            async with self.session_factory() as db:
                return await db.scalar(statement) or 0
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to count artifact items: {exc}") from exc

    async def replace_items(self, artifact_id: str, items: list[dict]) -> int:
        statement = (
            update(ArtifactRow).where(ArtifactRow.artifact_id == artifact_id).values(items=items, updated_at=func.now())
        )

        try:
            async with self.session_factory.begin() as db:
                await db.execute(statement)

            return len(items)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to replace artifact items: {exc}") from exc

    async def finish_artifact(self, artifact_id: str, status: str, error: str = "") -> None:
        statement = (
            update(ArtifactRow)
            .where(ArtifactRow.artifact_id == artifact_id)
            .values(status=status, error=error, updated_at=func.now())
        )

        try:
            async with self.session_factory.begin() as db:
                await db.execute(statement)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to finish artifact: {exc}") from exc

    async def delete_artifacts_for_chat(self, chat_id: str) -> int:
        statement = delete(ArtifactRow).where(ArtifactRow.chat_id == chat_id)

        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(statement)

                return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete artifacts: {exc}") from exc
