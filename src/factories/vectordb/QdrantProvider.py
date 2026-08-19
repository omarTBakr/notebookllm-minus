import uuid
from pathlib import Path
from time import perf_counter

from qdrant_client import AsyncQdrantClient, models  # ty: ignore[unresolved-import]

from enums import DistanceMethod, DistanceFunction
from exceptions import VectorDBError

from .VectorDBInterface import VectorDBInterface

# Qdrant only accepts an unsigned integer or a UUID as a point id. Chunk ids
# from Mongo are 24-hex ObjectIds, which are neither, so non-UUID strings are
# hashed into this namespace. uuid5 is deterministic, so re-processing an asset
# overwrites its old points instead of duplicating them.
_ID_NAMESPACE = uuid.NAMESPACE_OID


class QdrantProvider(VectorDBInterface):
    """Vector storage backed by Qdrant.

    Runs in either of two modes, chosen by what is configured:

    * **embedded** — ``path`` points at a local directory, no server involved.
      This is the default, so a fresh checkout works with nothing else running.
    * **server** — ``url`` (plus optional ``api_key``) points at a Qdrant
      instance or Cloud cluster.

    The two are mutually exclusive in the client, so setting both is rejected
    up front rather than silently preferring one.
    """
    #replaced by DistanceFunction enum
    # _DISTANCES = {
    #     DistanceMethod.COSINE: models.Distance.COSINE,
    #     DistanceMethod.DOT: models.Distance.DOT,
    #     DistanceMethod.EUCLID: models.Distance.EUCLID,
    # }

    def __init__(
        self,
        path: str | Path | None = None,
        url: str | None = None,
        api_key: str | None = None,
        distance_method: DistanceMethod = DistanceMethod.COSINE,
    ) -> None:

        super().__init__(distance_method=distance_method)

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

        self.client: AsyncQdrantClient | None = None

    # --- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:

        if self.client is not None:
            return

        try:
            if self.url:
                self.client = AsyncQdrantClient(url=self.url, api_key=self.api_key)
                self.logger.info("Connected to Qdrant server at %s", self.url)
            else:
                # The client will not create a missing parent itself.
                self.path.mkdir(parents=True, exist_ok=True)
                self.client = AsyncQdrantClient(path=str(self.path))
                self.logger.info("Opened embedded Qdrant store at %s", self.path)

        except Exception as exc:
            raise VectorDBError(f"Could not open Qdrant: {exc}") from exc

    async def disconnect(self) -> None:

        # Safe to call when connect() never ran or already failed.
        if self.client is None:
            return

        try:
            await self.client.close()

        except Exception as exc:
            raise VectorDBError(f"Closing Qdrant failed: {exc}") from exc

        finally:
            self.client = None
            self.logger.info("Closed Qdrant connection")
 


    def _require_client(self) -> AsyncQdrantClient:

        if self.client is None:
            raise VectorDBError("Qdrant client is not connected; call connect() first")

        return self.client


    # --- collections ---------------------------------------------------------

    async def collection_exists(self, collection_name: str) -> bool:

        client = self._require_client()

        try:
            return await client.collection_exists(collection_name=collection_name)

        except Exception as exc:
            raise VectorDBError(f"Qdrant collection_exists({collection_name!r}) failed: {exc}") from exc




    async def list_collections(self) -> list[str]:

        client = self._require_client()

        try:
            response = await client.get_collections()

        except Exception as exc:
            raise VectorDBError(f"Qdrant get_collections failed: {exc}") from exc

        return [collection.name for collection in response.collections]




    async def get_collection_info(self, collection_name: str) -> dict:

        client = self._require_client()

        try:
            info = await client.get_collection(collection_name=collection_name)

        except Exception as exc:
            raise VectorDBError(f"Qdrant get_collection({collection_name!r}) failed: {exc}") from exc

        return info.model_dump()





    async def create_collection(
        self, collection_name: str, embedding_size: int, reset: bool = False
    ) -> bool:

        client = self._require_client()

        if await self.collection_exists(collection_name):

            if not reset:
                self.logger.debug(
                    "Qdrant collection %r already exists; leaving it as is", collection_name
                )
                return False

            self.logger.info(
                "Resetting Qdrant collection %r — existing points will be dropped",
                collection_name,
            )
            await self.delete_collection(collection_name)

        try:
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=embedding_size,
                    # distance=self._DISTANCES[self.distance_method],
                    distance=DistanceFunction[self.distance_method.upper()],
                ),
            )

        except Exception as exc:
            raise VectorDBError(f"Qdrant create_collection({collection_name!r}) failed: {exc}") from exc

        self.logger.info(
            "Created Qdrant collection %r (size=%d, distance=%s)",
            collection_name,
            embedding_size,
            self.distance_method.value,
        )

        return True

    async def delete_collection(self, collection_name: str) -> bool:

        client = self._require_client()

        try:
            deleted = await client.delete_collection(collection_name=collection_name)

        except Exception as exc:
            raise VectorDBError(f"Qdrant delete_collection({collection_name!r}) failed: {exc}") from exc

        self.logger.info("Deleted Qdrant collection %r", collection_name)

        return bool(deleted)

    # --- records -------------------------------------------------------------

    async def insert_one(
        self,
        collection_name: str,
        text: str,
        vector: list[float],
        metadata: dict | None = None,
        record_id: str | None = None,
    ) -> bool:

        return await self.insert_many(
            collection_name=collection_name,
            texts=[text],
            vectors=[vector],
            metadata=[metadata or {}],
            record_ids=[record_id] if record_id is not None else None,
        )

        

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

        # Mismatched lengths would zip silently and drop the tail, so refuse.
        if len(texts) != len(vectors):
            raise VectorDBError(
                f"insert_many got {len(texts)} texts but {len(vectors)} vectors"
            )

        if metadata is not None and len(metadata) != len(texts):
            raise VectorDBError(
                f"insert_many got {len(texts)} texts but {len(metadata)} metadata entries"
            )

        if record_ids is not None and len(record_ids) != len(texts):
            raise VectorDBError(
                f"insert_many got {len(texts)} texts but {len(record_ids)} record ids"
            )

        if not texts:
            return True

        points = [
            models.PointStruct(
                id=self._point_id(record_ids[index] if record_ids else None),
                vector=vectors[index],
                payload={
                    "text": texts[index],
                    "metadata": metadata[index] if metadata else {},
                },
            )
            for index in range(len(texts))
        ]

        # Batched so a large asset does not become one enormous request.
        for start in range(0, len(points), batch_size):

            batch = points[start : start + batch_size]

            try:
                await client.upsert(collection_name=collection_name, points=batch)

            except Exception as exc:
                raise VectorDBError(
                    f"Qdrant upsert into {collection_name!r} failed at offset {start}: {exc}"
                ) from exc

        self.logger.info(
            "Upserted %d points into Qdrant collection %r", len(points), collection_name
        )

        return True
        


    async def delete_by_metadata(
        self, collection_name: str, key: str, value: str
    ) -> int:

        client = self._require_client()

        # Deleting from a collection that was never created is a no-op, not a
        # failure — the caller's intent (no points with this key) already holds.
        if not await self.collection_exists(collection_name):
            return 0

        # Payloads are stored as {"text": ..., "metadata": {...}}, so the
        # filter has to address the nested key by path.
        selector = models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key=f"metadata.{key}", match=models.MatchValue(value=value)
                    )
                ]
            )
        )

        # Qdrant's delete does not report a count, so read it first. Only used
        # for logging and the API response; the delete is authoritative.
        try:
            before = (await client.count(collection_name=collection_name)).count

            await client.delete(collection_name=collection_name, points_selector=selector)

            after = (await client.count(collection_name=collection_name)).count

        except Exception as exc:
            raise VectorDBError(
                f"Qdrant delete from {collection_name!r} where {key}={value!r} failed: {exc}"
            ) from exc

        removed = before - after

        self.logger.info(
            "Deleted %d point(s) from %r where metadata.%s=%r",
            removed,
            collection_name,
            key,
            value,
        )

        return removed

    async def search_by_vector(
        self, collection_name: str, vector: list[float], limit: int = 5
    ) -> list[dict]:

        client = self._require_client()

        started = perf_counter()

        try:
            # query_points, not the search() removed in qdrant-client 1.19.
            response = await client.query_points(
                collection_name=collection_name,
                query=vector,
                limit=limit,
                with_payload=True,
            )

        except Exception as exc:
            raise VectorDBError(f"Qdrant search in {collection_name!r} failed: {exc}") from exc

        elapsed_ms = (perf_counter() - started) * 1000

        hits = [
            {
                "id": point.id,
                "score": point.score,
                "text": (point.payload or {}).get("text"),
                "metadata": (point.payload or {}).get("metadata", {}),
            }
            for point in response.points
        ]

        # The read path runs on every question asked, so it gets the same
        # counts-and-timing treatment as the LLM calls — and no payload text.
        self.logger.debug(
            "Searched %r: %d/%d hits in %.0f ms (top_score=%s)",
            collection_name,
            len(hits),
            limit,
            elapsed_ms,
            round(hits[0]["score"], 4) if hits else None,
            extra={
                "collection": collection_name,
                "duration_ms": round(elapsed_ms, 1),
                "hit_count": len(hits),
                "limit": limit,
            },
        )

        if not hits:
            # Not an error — but an empty result for a question the user asked
            # is the first thing to check when answers look wrong.
            self.logger.warning(
                "Qdrant search returned no hits from collection %r", collection_name
            )

        return hits




        

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _point_id(record_id: str | None) -> str:
        """Turn an arbitrary id into one Qdrant will accept."""

        if record_id is None:
            return str(uuid.uuid4())

        try:
            return str(uuid.UUID(str(record_id)))

        except ValueError:
            # Not a UUID (a Mongo ObjectId, say) — derive one deterministically.
            return str(uuid.uuid5(_ID_NAMESPACE, str(record_id)))
