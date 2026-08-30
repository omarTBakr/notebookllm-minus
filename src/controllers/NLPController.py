import re
from typing import Callable

from bson.objectid import ObjectId  # ty: ignore[unresolved-import]

from enums import EmbeddingInputType
from exceptions import NotFoundError
from factories.llmembedding import LLMEmbeddingInterface
from factories.db.interfaces import VectorRepository
from models import ChunkModel

from time import perf_counter

from utils.metrics import INGEST_CHUNKS, VECTOR_UPSERT_SECONDS
from .BaseController import BaseController

# Qdrant collection names are far more restricted than a URL path segment, and
# project_id is free-form user input, so anything outside this set is folded to
# an underscore before it reaches the vector store.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


class NLPController(BaseController):
    """Turns a project's stored chunks into vectors, and searches them back out.

    Takes both clients rather than reading them off the app object, so the
    routes stay thin and this is exercisable with fakes.
    """

    def __init__(
        self,
        embedding_client: LLMEmbeddingInterface,
        vectordb_client: VectorRepository,
    ) -> None:

        super().__init__()

        self.embedding_client = embedding_client

        self.vectordb_client = vectordb_client

    # --- naming ---------------------------------------------------------------

    @staticmethod
    def collection_name(project_id: str) -> str:
        """The Qdrant collection holding one project's vectors.

        One collection per project: resetting or deleting a project cannot
        touch another's vectors, and the collection's own point count is the
        project's point count with no filtering involved.
        """
        return f"project_{_UNSAFE_NAME_CHARS.sub('_', str(project_id))}"

    # --- indexing -------------------------------------------------------------

    async def index_chunks(
        self,
        chunk_model: ChunkModel,
        project_object_id: ObjectId,
        project_id: str,
        asset_id: str | None = None,
        reset: bool = False,
        batch_size: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Embed a project's chunks and upsert them into the vector store.

        Streams rather than loading every chunk at once: a project is embedded
        `batch_size` at a time, so peak memory is one batch of text plus one
        batch of vectors, not the whole corpus.

        Idempotent without *reset*. Each point is keyed on the chunk's Mongo
        ``_id``, which QdrantProvider hashes deterministically, so re-running
        this overwrites the same points instead of duplicating them.
        """
        # None rather than a literal default, so the .env value applies unless
        # a caller deliberately overrides it.
        batch_size = batch_size or self.settings.CHUNKING_BATCH_SIZE

        collection = self.collection_name(project_id)

        # The vector width comes from the client, not from Settings: the client
        # is what validates the model's actual output, so taking it from here
        # means the collection can never be created at a width the model then
        # fails to match.
        vector_size = self.embedding_client.embedding_size

        # A reset must only clear what this call will rebuild. Dropping the
        # whole collection for an asset-scoped push would delete every *other*
        # asset's vectors and never put them back, since the loop below only
        # walks the one asset — the same trap routes/process.py:117 avoids by
        # scoping its chunk delete to the asset being processed.
        drop_collection = reset and asset_id is None

        created = await self.vectordb_client.create_collection(
            collection_name=collection, embedding_size=vector_size, reset=drop_collection
        )

        removed = 0
        if asset_id is not None and not created:
            # Clear this asset's existing points before re-adding them. Needed
            # even without *reset*: re-chunking an asset can produce fewer
            # chunks than before, and the surplus tail would otherwise linger
            # as vectors pointing at text that no longer exists.
            removed = await self.vectordb_client.delete_by_metadata(
                collection_name=collection, key="asset_id", value=asset_id
            )

        self.logger.info(
            "Indexing project %r into %r (asset_id=%r, reset=%s, new_collection=%s, cleared=%d)",
            project_id,
            collection,
            asset_id,
            reset,
            created,
            removed,
        )

        # How many chunks this call will embed. Counted up front so a caller
        # watching *on_progress* can show a real fraction rather than a
        # spinner: this loop streams, so without it there is no denominator.
        expected = await chunk_model.count_project_chunks(project_object_id, asset_id)

        total = 0
        batches = 0
        batch: list = []

        if on_progress:
            on_progress(0, expected)

        async for chunk in chunk_model.iter_project_chunks(project_object_id, asset_id):

            batch.append(chunk)

            if len(batch) >= batch_size:
                await self._flush(collection, batch)
                total += len(batch)
                batches += 1
                batch = []
                if on_progress:
                    on_progress(total, expected)

        # Whatever is left over after the last full batch.
        if batch:
            await self._flush(collection, batch)
            total += len(batch)
            batches += 1
            if on_progress:
                on_progress(total, expected)

        # Chunk count only. The "indexing" *duration* is observed by
        # routes/chat/_helpers when it closes the stage — recording it here as
        # well put two observations in the histogram for one upload, which
        # halves the apparent mean and doubles the rate.
        INGEST_CHUNKS.observe(total)

        self.logger.info(
            "Indexed %d chunk(s) for project %r in %d batch(es)", total, project_id, batches
        )

        return {
            "collection": collection,
            "chunks_indexed": total,
            "points_cleared": removed,
            "batches": batches,
            "vector_size": vector_size,
            "collection_created": created,
        }

    async def _flush(self, collection: str, batch: list) -> None:
        """Embed one batch of chunks and upsert it."""

        texts = [chunk.chunk_content for chunk in batch]

        vectors = await self.embedding_client.embed(texts, EmbeddingInputType.DOCUMENT)

        # Everything a citation will eventually need, and nothing that would
        # duplicate the chunk text already stored in the payload.
        metadata = [
            {
                "project_id": str(chunk.project_id),
                "asset_id": chunk.asset_id,
                "chunk_order": chunk.chunk_order,
                "source": (chunk.chunk_metadata or {}).get("source"),
            }
            for chunk in batch
        ]

        # Only the write. The embed above is measured one layer down in
        # LLMEmbeddingInterface, and folding it in here would report ~95% of
        # the ingest time as "vector upsert latency" — true of the clock, and
        # badly wrong about where the time went.
        _upsert_started = perf_counter()

        await self.vectordb_client.insert_many(
            collection_name=collection,
            texts=texts,
            vectors=vectors,
            metadata=metadata,
            record_ids=[self._point_key(chunk) for chunk in batch],
        )

        VECTOR_UPSERT_SECONDS.labels(type(self.vectordb_client).__name__).observe(
            perf_counter() - _upsert_started
        )

    @staticmethod
    def _point_key(chunk) -> str:
        """A point id that survives re-processing.

        Deliberately *not* the chunk's Mongo _id: /process with reset=true
        deletes the chunks and re-inserts them with brand-new ObjectIds, so an
        _id-derived point id would change on every re-process and leave the old
        vectors stranded in the collection forever — searchable, and quoting
        text that no longer exists.

        (asset_id, chunk_order) names the same passage across re-runs, so the
        upsert overwrites in place. Falls back to the _id for legacy chunks
        written before asset_id existed, which have nothing stabler to use.
        """
        if chunk.asset_id:
            return f"{chunk.asset_id}:{chunk.chunk_order}"

        return str(chunk.id)

    # --- reading --------------------------------------------------------------

    async def search(
        self,
        project_id: str,
        text: str,
        limit: int = 5,
        asset_ids: list[str] | None = None,
    ) -> list[dict]:
        """Nearest chunks to *text*, best first.

        *asset_ids* restricts the search to those sources; None searches all.
        """

        collection = self.collection_name(project_id)

        # An unindexed project and a project with no matches are different
        # answers to different questions; returning [] for both would hide a
        # forgotten push behind what looks like a poor query.
        if not await self.vectordb_client.collection_exists(collection):
            raise NotFoundError(
                f"Project {project_id!r} has no vector index yet — "
                f"POST /nlp/index/push/{project_id} first"
            )

        # QUERY, not DOCUMENT: asymmetric models embed the two differently, and
        # using the document form for a question quietly degrades recall.
        vectors = await self.embedding_client.embed([text], EmbeddingInputType.QUERY)

        hits = await self.vectordb_client.search_by_vector(
            collection_name=collection,
            vector=vectors[0],
            limit=limit,
            asset_ids=asset_ids,
        )

        # Without a floor an unrelated question still returns `limit` passages,
        # and the RAG prompt presents whatever it is given as source material —
        # so the model answers confidently from the five least-bad chunks in the
        # corpus. Dropping them lets the ungrounded path take over instead,
        # which is the honest answer.
        floor = self.settings.RETRIEVAL_MIN_SCORE
        if floor:
            kept = [h for h in hits if h.get("score") is not None and h["score"] >= floor]
            if len(kept) != len(hits):
                self.logger.info(
                    "Retrieval floor dropped %d of %d passage(s) below %.3f",
                    len(hits) - len(kept),
                    len(hits),
                    floor,
                )
            return kept

        return hits

    async def get_index_info(self, project_id: str) -> dict:
        """What the vector store holds for this project."""

        collection = self.collection_name(project_id)

        if not await self.vectordb_client.collection_exists(collection):
            # Not an error: "is this indexed?" is the question, and no is an
            # answer. The route reports it as a 200.
            return {"collection": collection, "exists": False}

        info = await self.vectordb_client.get_collection_info(collection)

        return {"collection": collection, "exists": True, "info": info}
