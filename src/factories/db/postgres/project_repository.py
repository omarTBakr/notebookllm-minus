from bson.objectid import ObjectId  # ty: ignore[unresolved-import]

from sqlalchemy import cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import SQLAlchemyError

from exceptions import ProjectNotFoundError, DbError
from models.db_schema import Project
from .base_repository import PostgresBaseRepository, ProjectRow
from ..interfaces.project_repository import ProjectRepository


class PostgresProjectRepository(PostgresBaseRepository, ProjectRepository):
    """PostgreSQL implementation of ProjectRepository."""

    @staticmethod
    def _id_strings(object_ids) -> list[str]:
        """ObjectId is not JSON-serialisable; the columns hold the hex form."""
        return [str(oid) for oid in object_ids]

    async def create_project(self, project: Project) -> str:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    insert(ProjectRow).values(
                        id=self._generate_id(),
                        project_id=project.project_id,
                        name=project.name,
                        description=project.description,
                        chunks_ids=self._id_strings(project.chunks_ids),
                        assets_ids=self._id_strings(project.assets_ids),
                        created_at=project.created_at,
                        updated_at=project.updated_at,
                    )
                )
            return project.project_id
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create project: {exc}") from exc

    async def update_project(self, project: Project) -> ObjectId:
        """Create the project or update it, and return its row id.

        An upsert, matching the Mongo behaviour: the ingest routes call this to
        make sure a project exists before attaching an asset, so a first upload
        must create it rather than 404. It returns the row's ObjectId because
        that is what DataChunk.project_id is, not the business project_id.
        """
        statement = insert(ProjectRow).values(
            id=self._generate_id(),
            project_id=project.project_id,
            name=project.name,
            description=project.description,
            chunks_ids=self._id_strings(project.chunks_ids),
            assets_ids=self._id_strings(project.assets_ids),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
        # The id lists are deliberately not overwritten on conflict: a re-upload
        # must not forget the chunks and assets already attached.
        statement = statement.on_conflict_do_update(
            index_elements=["project_id"],
            set_={
                "name": statement.excluded.name,
                "description": statement.excluded.description,
                "updated_at": statement.excluded.updated_at,
            },
        ).returning(ProjectRow.id)

        try:
            async with self.session_factory.begin() as db:
                row_id = await db.scalar(statement)
            return ObjectId(row_id)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to update project: {exc}") from exc

    async def get_project(self, project_id: str) -> Project:
        try:
            async with self.session_factory() as db:
                row = await db.scalar(
                    select(ProjectRow).where(ProjectRow.project_id == project_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to get project: {exc}") from exc

        if row is None:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

        return self._record_to_model(row, Project)

    async def list_projects(self) -> list[Project]:
        try:
            async with self.session_factory() as db:
                rows = (
                    await db.scalars(
                        select(ProjectRow).order_by(ProjectRow.created_at.desc())
                    )
                ).all()
                return self._records_to_models(list(rows), Project)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to list projects: {exc}") from exc

    async def rename(self, project_id: str, name: str) -> None:
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    update(ProjectRow)
                    .where(ProjectRow.project_id == project_id)
                    .values(name=name, updated_at=func.now())
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to rename project: {exc}") from exc

        if result.rowcount == 0:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

    async def delete_project(self, project_id: str) -> None:
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    delete(ProjectRow).where(ProjectRow.project_id == project_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete project: {exc}") from exc

        if result.rowcount == 0:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

    async def _append_ids(self, project_id: str, column, ids: list[str], what: str) -> None:
        """Append to one of the JSONB id arrays, server-side.

        `||` on jsonb concatenates, so this is one statement and cannot lose a
        concurrent append the way read-modify-write would. The column is NOT
        NULL DEFAULT '[]', so there is nothing to COALESCE away.
        """
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    update(ProjectRow)
                    .where(ProjectRow.project_id == project_id)
                    .values(
                        {
                            column: column.op("||")(cast(ids, JSONB)),
                            "updated_at": func.now(),
                        }
                    )
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to add {what} to project: {exc}") from exc

        if result.rowcount == 0:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")

    async def add_asset_id(self, project_id: str, asset_object_id: str) -> None:
        await self._append_ids(
            project_id, ProjectRow.assets_ids, [str(asset_object_id)], "asset id"
        )

    async def add_chunk_ids(self, project_id: str, chunk_object_ids: list[str]) -> None:
        if not chunk_object_ids:
            return

        await self._append_ids(
            project_id, ProjectRow.chunks_ids, self._id_strings(chunk_object_ids), "chunk ids"
        )
