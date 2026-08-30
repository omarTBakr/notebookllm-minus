"""Qdrant-backed VectorRepository — used by the Mongo provider.

When the document store is MongoDB, vectors live in Qdrant. This class wraps
the Qdrant client and implements the VectorRepository interface so the rest of
the application never imports qdrant_client directly.
"""

import uuid
from pathlib import Path
from time import perf_counter

from qdrant_client import AsyncQdrantClient, models  # ty: ignore[unresolved-import]

from enum import Enum
from enums import DistanceMethod, IndexType
from exceptions import DbConnectionError, DbError, UnsupportedProviderError
from utils import get_logger

from ..interfaces.vector_repository import VectorRepository

_ID_NAMESPACE = uuid.NAMESPACE_OID


class DistanceFunction(str, Enum):
    """Qdrant distance functions."""

    COSINE  = models.Distance.COSINE
    DOT     = models.Distance.DOT
    EUCLID  = models.Distance.EUCLID


class QdrantVectorRepository(VectorRepository):
    """VectorRepository backed by Qdrant (embedded or server)."""

    def __init__(
        self,
        path: str | Path | None = None,
        url: str | None = None,
        api_key: str | None = None,
        distance_method: DistanceMethod = DistanceMethod.COSINE,
        index_type: IndexType = IndexType.HNSW,
    ) -> None:
        if url and path:
            raise ValueError(
                "Qdrant takes either VECTOR_DB_URL (server mode) or "
                "VECTOR_DB_PATH (embedded mode), not both"
            )
        if not url and not path:
            raise ValueError("Qdrant needs one of VECTOR_DB_URL or VECTOR_DB_PATH")

        self.path = Path(path) if path else None
        self.url = url
        self.api_key = api_key
        self.distance_method = DistanceMethod(distance_method)
        self.index_type = IndexType(index_type)
        self.client: AsyncQdrantClient | None = None
        self.logger = get_logger(type(self).__module__)

    # --- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        if self.client is not None:
            return
        try:
            if self.url:
                self.client = AsyncQdrantClient(url=self.url, api_key=self.api_key)
                self.logger.info("Connected to Qdrant server at %s", self.url)
            else:
                self.path.mkdir(parents=True, exist_ok=True)
                self.client = AsyncQdrantClient(path=str(self.path))
                self.logger.info("Opened embedded Qdrant store at %s", self.path)
        except Exception as exc:
            raise DbConnectionError(f"Could not open Qdrant: {exc}") from exc

    async def disconnect(self) -> None:
        if self.client is None:
            return
        try:
            await self.client.close()
        except Exception as exc:
            raise DbError(f"Closing Qdrant failed: {exc}") from exc
        finally:
            self.client = None
            self.logger.info("Closed Qdrant connection")

    def _require_client(self) -> AsyncQdrantClient:
        if self.client is None:
            raise DbConnectionError("Qdrant client is not connected; call connect() first")
        return self.client

    # --- collections ---------------------------------------------------------

    async def collection_exists(self, collection_name: str) -> bool:
        client = self._require_client()
        try:
            return await client.collection_exists(collection_name=collection_name)
        except Exception as exc:
            raise DbError(f"collection_exists({collection_name!r}) failed: {exc}") from exc

    async def list_collections(self) -> list[str]:
        client = self._require_client()
        try:
            response = await client.get_collections()
        except Exception as exc:
            raise DbError(f"list_collections failed: {exc}") from exc
        return [c.name for c in response.collections]

    async def get_collection_info(self, collection_name: str) -> dict:
        client = self._require_client()
        try:
            info = await client.get_collection(collection_name=collection_name)
        except Exception as exc:
            raise DbError(f"get_collection_info({collection_name!r}) failed: {exc}") from exc
        return info.model_dump()

    async def create_collection(
        self, collection_name: str, embedding_size: int, reset: bool = False
    ) -> bool:
        client = self._require_client()
        if await self.collection_exists(collection_name):
            if not reset:
                self.logger.debug("Collection %r already exists; skipping", collection_name)
                return False
            self.logger.info("Resetting collection %r", collection_name)
            await self.delete_collection(collection_name)
        try:
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=embedding_size,
                    distance=DistanceFunction[self.distance_method.upper()],
                ),
                # Indexing starts disabled — building the HNSW graph
                # incrementally as insert_many() streams rows in is far
                # slower than inserting first and indexing once via
                # create_index(). This is Qdrant's own documented bulk-upload
                # pattern (indexing_threshold=0 means "never auto-index").
                optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0),
            )
        except Exception as exc:
            raise DbError(f"create_collection({collection_name!r}) failed: {exc}") from exc
        self.logger.info(
            "Created collection %r (size=%d, distance=%s)",
            collection_name, embedding_size, self.distance_method,
        )
        return True

    # Qdrant's own default indexing_threshold (vector count per segment
    # before HNSW is built automatically) — restored here once indexing is
    # deliberately turned back on.
    _DEFAULT_INDEXING_THRESHOLD = 20000

    async def create_index(
        self,
        collection_name: str,
        embedding_size: int,
        index_type: IndexType | None = None,
        reset: bool = False,
    ) -> bool:
        chosen = IndexType(index_type) if index_type is not None else self.index_type
        if chosen is not IndexType.HNSW:
            raise UnsupportedProviderError(
                f"Qdrant only builds HNSW indexes, got {chosen.value!r}"
            )

        client = self._require_client()
        try:
            # Restoring the default threshold is enough to (re-)trigger a
            # build even if indexing was already on — no separate "reset"
            # path needed, unlike pgvector where DROP/CREATE INDEX are
            # explicit statements.
            await client.update_collection(
                collection_name=collection_name,
                optimizers_config=models.OptimizersConfigDiff(
                    indexing_threshold=self._DEFAULT_INDEXING_THRESHOLD
                ),
            )
        except Exception as exc:
            raise DbError(f"create_index({collection_name!r}) failed: {exc}") from exc
        self.logger.info("Enabled HNSW indexing for %r", collection_name)
        return True

    async def delete_collection(self, collection_name: str) -> bool:
        client = self._require_client()
        try:
            deleted = await client.delete_collection(collection_name=collection_name)
        except Exception as exc:
            raise DbError(f"delete_collection({collection_name!r}) failed: {exc}") from exc
        self.logger.info("Deleted collection %r", collection_name)
        return bool(deleted)

    # --- records -------------------------------------------------------------

    async def insert_many(
        self,
        collection_name: str,
        texts: list[str],
        vectors: list[list[float]],
        metadata: list[dict] | None = None,
        record_ids: list[str] | None = None,
        batch_size: int = 64,
    ) -> bool:
        client = self._require_client()
        if len(texts) != len(vectors):
            raise DbError(f"insert_many: {len(texts)} texts but {len(vectors)} vectors")
        if metadata is not None and len(metadata) != len(texts):
            raise DbError(f"insert_many: {len(texts)} texts but {len(metadata)} metadata entries")
        if record_ids is not None and len(record_ids) != len(texts):
            raise DbError(f"insert_many: {len(texts)} texts but {len(record_ids)} record ids")
        if not texts:
            return True

        points = [
            models.PointStruct(
                id=self._point_id(record_ids[i] if record_ids else None),
                vector=vectors[i],
                payload={"text": texts[i], "metadata": metadata[i] if metadata else {}},
            )
            for i in range(len(texts))
        ]
        for start in range(0, len(points), batch_size):
            try:
                await client.upsert(collection_name=collection_name, points=points[start:start + batch_size])
            except Exception as exc:
                raise DbError(f"insert_many into {collection_name!r} failed at offset {start}: {exc}") from exc
        self.logger.info("Upserted %d points into %r", len(points), collection_name)
        return True

    async def delete_by_metadata(self, collection_name: str, key: str, value: str) -> int:
        client = self._require_client()
        if not await self.collection_exists(collection_name):
            return 0
        selector = models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value))]
            )
        )
        try:
            before = (await client.count(collection_name=collection_name)).count
            await client.delete(collection_name=collection_name, points_selector=selector)
            after = (await client.count(collection_name=collection_name)).count
        except Exception as exc:
            raise DbError(f"delete_by_metadata from {collection_name!r} where {key}={value!r} failed: {exc}") from exc
        removed = before - after
        self.logger.info("Deleted %d point(s) from %r where metadata.%s=%r", removed, collection_name, key, value)
        return removed

    async def search_by_vector(
        self,
        collection_name: str,
        vector: list[float],
        limit: int = 5,
        asset_ids: list[str] | None = None,
    ) -> list[dict]:
        client = self._require_client()
        query_filter = None
        if asset_ids is not None:
            query_filter = models.Filter(
                must=[models.FieldCondition(key="metadata.asset_id", match=models.MatchAny(any=list(asset_ids)))]
            )
        started = perf_counter()
        try:
            response = await client.query_points(
                collection_name=collection_name,
                query=vector,
                limit=limit,
                with_payload=True,
                query_filter=query_filter,
            )
        except Exception as exc:
            raise DbError(f"search_by_vector in {collection_name!r} failed: {exc}") from exc
        elapsed_ms = (perf_counter() - started) * 1000
        hits = [
            {"id": p.id, "score": p.score, "text": (p.payload or {}).get("text"), "metadata": (p.payload or {}).get("metadata", {})}
            for p in response.points
        ]
        self.logger.debug(
            "Searched %r: %d/%d hits in %.0f ms",
            collection_name, len(hits), limit, elapsed_ms,
        )
        if not hits:
            self.logger.warning("search_by_vector returned no hits from %r", collection_name)
        return hits

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _point_id(record_id: str | None) -> str:
        if record_id is None:
            return str(uuid.uuid4())
        try:
            return str(uuid.UUID(str(record_id)))
        except ValueError:
            return str(uuid.uuid5(_ID_NAMESPACE, str(record_id)))
