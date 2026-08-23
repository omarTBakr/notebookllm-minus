import json

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]

from exceptions import ProjectNotFoundError, DbError
from models.db_schema import Project
from .base_repository import PostgresBaseRepository
from ..interfaces.project_repository import ProjectRepository


class PostgresProjectRepository(PostgresBaseRepository, ProjectRepository):
    """PostgreSQL implementation of ProjectRepository."""

    async def create_project(self, project: Project) -> str:
        record_id = self._generate_id()
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO projects (id, project_id, name, description, chunks_ids, assets_ids, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    record_id,
                    project.project_id,
                    project.name,
                    project.description,
                    json.dumps([str(c) for c in project.chunks_ids]),
                    json.dumps([str(a) for a in project.assets_ids]),
                    project.created_at,
                    project.updated_at,
                )
            return project.project_id
        except Exception as exc:
            raise DbError(f"Failed to create project: {exc}") from exc

    async def update_project(self, project: Project) -> ObjectId:
        """Create the project or update it, and return its row id.

        An upsert, matching the Mongo behaviour: the ingest routes call this to
        make sure a project exists before attaching an asset, so a first upload
        must create it rather than 404. It returns the row's ObjectId because
        that is what DataChunk.project_id is, not the business project_id.
        """
        record_id = self._generate_id()
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO projects (id, project_id, name, description,
                                          chunks_ids, assets_ids, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (project_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id
                    """,
                    record_id,
                    project.project_id,
                    project.name,
                    project.description,
                    json.dumps([str(c) for c in project.chunks_ids]),
                    json.dumps([str(a) for a in project.assets_ids]),
                    project.created_at,
                    project.updated_at,
                )
            return ObjectId(row["id"])
        except Exception as exc:
            raise DbError(f"Failed to update project: {exc}") from exc

    async def get_project(self, project_id: str) -> Project:
        try:
            async with self.pool.acquire() as conn:
                record = await conn.fetchrow(
                    "SELECT * FROM projects WHERE project_id = $1", project_id
                )
        except Exception as exc:
            raise DbError(f"Failed to get project: {exc}") from exc

        if not record:
            raise ProjectNotFoundError(f"Project {project_id!r} not found")
        
        return self._record_to_model(record, Project)

    async def list_projects(self) -> list[Project]:
        try:
            async with self.pool.acquire() as conn:
                records = await conn.fetch("SELECT * FROM projects ORDER BY created_at DESC")
                return self._records_to_models(records, Project)
        except Exception as exc:
            raise DbError(f"Failed to list projects: {exc}") from exc

    async def rename(self, project_id: str, name: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE projects SET name = $1, updated_at = CURRENT_TIMESTAMP WHERE project_id = $2",
                    name,
                    project_id,
                )
                if result == "UPDATE 0":
                    raise ProjectNotFoundError(f"Project {project_id!r} not found")
        except ProjectNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to rename project: {exc}") from exc

    async def delete_project(self, project_id: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM projects WHERE project_id = $1",
                    project_id,
                )
                if result == "DELETE 0":
                    raise ProjectNotFoundError(f"Project {project_id!r} not found")
        except ProjectNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to delete project: {exc}") from exc

    async def add_asset_id(self, project_id: str, asset_object_id: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                # We can append to the JSONB array using Postgres jsonb operator `||`
                result = await conn.execute(
                    """
                    UPDATE projects 
                    SET assets_ids = COALESCE(assets_ids, '[]'::jsonb) || $1::jsonb,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = $2
                    """,
                    # ObjectId is not JSON-serialisable; the column holds
                    # the 24-hex string form.
                    json.dumps([str(asset_object_id)]),
                    project_id,
                )
                if result == "UPDATE 0":
                    raise ProjectNotFoundError(f"Project {project_id!r} not found")
        except ProjectNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to add asset id to project: {exc}") from exc

    async def add_chunk_ids(self, project_id: str, chunk_object_ids: list[str]) -> None:
        if not chunk_object_ids:
            return
            
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE projects 
                    SET chunks_ids = COALESCE(chunks_ids, '[]'::jsonb) || $1::jsonb,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = $2
                    """,
                    json.dumps([str(c) for c in chunk_object_ids]),
                    project_id,
                )
                if result == "UPDATE 0":
                    raise ProjectNotFoundError(f"Project {project_id!r} not found")
        except ProjectNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to add chunk ids to project: {exc}") from exc
