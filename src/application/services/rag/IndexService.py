from time import perf_counter

from data.models import ChatModel, ChunkModel, ProjectModel
from shared.enums import EmbeddingInputType
from shared.exceptions import ChatNotFoundError, ProjectNotFoundError

from ..core import BaseService


class IndexService(BaseService):
    """What a project's vector index holds, whether it can be built, and whether
    the backends behind it are alive."""

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.projects = ProjectModel(db)
        self.chunks = ChunkModel(db)

    async def chat_for_project(self, project_id: str):
        """The chat whose id is this project's, or None when the project is not a chat.

        /process and /data create projects that never had one.
        """
        try:
            return await ChatModel(self.db).get_chat(project_id)
        except ChatNotFoundError:
            return None

    async def require_project(self, project_id: str) -> None:
        """404s if there is no such project."""
        await self.projects.get_project(project_id)

    async def require_chunks(self, project_id: str, asset_id: str | None) -> None:
        """Validate existence before publishing, without holding the request
        while a provider embeds every chunk."""
        project = await self.projects.get_project(project_id)
        chunks_found = await self.chunks.count_project_chunks(project.id, asset_id)

        if not chunks_found:
            scope = f"asset {asset_id!r} of project {project_id!r}" if asset_id else f"Project {project_id!r}"
            raise ProjectNotFoundError(f"{scope} has no chunks to index - run /process/{project_id} first")

    async def info(self, project_id: str, nlp, embedding_model_id: str) -> dict:
        """What the vector store holds next to what the database holds.

        The gap between chunks in the database and points in the vector store is
        what tells you the index is stale, which is the reason to ask at all.
        """
        project = await self.projects.get_project(project_id)

        index = await nlp.get_index_info(project_id)
        chunks_in_db = await self.chunks.count_project_chunks(project.id)

        body = {
            "project_id": project_id,
            "collection": index["collection"],
            "indexed": index["exists"],
            "chunks_in_db": chunks_in_db,
            "embedding_model": embedding_model_id,
        }

        if index["exists"]:
            info = index["info"]
            vectors = (info.get("config", {}).get("params", {}) or {}).get("vectors", {}) or {}
            body.update(
                {
                    "points_count": info.get("points_count"),
                    "vector_size": vectors.get("size"),
                    "distance": vectors.get("distance"),
                    "status": info.get("status"),
                }
            )

        return body

    async def health(self, embedding_client) -> dict[str, dict]:
        """Live readiness of the three backends this pipeline depends on.

        The one place that catches exceptions instead of letting them reach the
        handler — *reporting* a backend's failure is the whole job here.
        """
        settings = self.settings
        checks: dict[str, dict] = {}

        # --- DB ---
        started = perf_counter()
        try:
            # A cheap round-trip that exercises both the document store and the
            # vector store connections.
            await self.db.vectors().list_collections()
            checks["db"] = {
                "status": "ok",
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "backend": settings.DOCUMENT_DB_BACKEND,
            }
        except Exception as exc:
            checks["db"] = {"status": "error", "backend": settings.DOCUMENT_DB_BACKEND, "error": str(exc)}

        # --- Embedding model ---
        # A real inference call, not a config echo: this is what catches "ollama
        # serve isn't running" and "the model was never pulled".
        started = perf_counter()
        try:
            vectors = await embedding_client.embed(["ping"], EmbeddingInputType.QUERY)
            checks["embedding"] = {
                "status": "ok",
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "provider": settings.EMBEDDING_BACKEND,
                "model": embedding_client.model_id,
                "dimensions": len(vectors[0]),
            }
        except Exception as exc:
            checks["embedding"] = {
                "status": "error",
                "provider": settings.EMBEDDING_BACKEND,
                "model": embedding_client.model_id,
                "error": str(exc),
            }

        # --- Vector store ---
        started = perf_counter()
        try:
            collections = await self.db.vectors().list_collections()
            checks["vectordb"] = {
                "status": "ok",
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "backend": settings.DOCUMENT_DB_BACKEND,
                "collections": len(collections),
            }
        except Exception as exc:
            checks["vectordb"] = {"status": "error", "backend": settings.DOCUMENT_DB_BACKEND, "error": str(exc)}

        return checks
