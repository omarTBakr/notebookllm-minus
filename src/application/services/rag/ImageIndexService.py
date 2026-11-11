"""A notebook's pictures as vectors, and the search over them.

The sibling of NLPService for the image collection. Kept apart because each
vector collection has exactly one width, and the multimodal embedder (global,
IMAGE_EMBEDDING_MODEL_ID) is independent of the text embedder a chat picked:
switching a notebook's text model rebuilds ``project_<id>`` and leaves
``project_<id>__img`` alone.

Points use the same key as their chunk, ``"{asset_id}:{chunk_order}"``, and
carry the caption as their text. A hit from here therefore merges with text
hits by id and goes through the same reranker and the same citation code.
"""

from __future__ import annotations

from collections import defaultdict

from application.providers.embedding.image import ImageEmbeddingInterface
from data.repositories.interfaces import VectorRepository
from shared.enums import EmbeddingInputType
from shared.utils.images import InvalidImageError

from ..core.BaseService import BaseService
from ..ingest.ImageRegionService import render_chunk
from .NLPService import NLPService

#: Pictures sent to the embedder per request.
EMBED_BATCH = 8


class ImageIndexService(BaseService):
    def __init__(self, embedding_client: ImageEmbeddingInterface, vectordb_client: VectorRepository) -> None:
        super().__init__()
        self.embedding_client = embedding_client
        self.vectordb_client = vectordb_client

    @staticmethod
    def collection_name(project_id: str) -> str:
        return f"{NLPService.collection_name(project_id)}__img"

    # --- indexing -------------------------------------------------------------

    async def index_images(
        self,
        chunk_model,
        asset_model,
        project_object_id,
        project_id: str,
        asset_id: str | None = None,
        reset: bool = False,
    ) -> dict:
        """Embed the image chunks of a project (or one asset) and upsert them."""
        collection = self.collection_name(project_id)
        size = self.embedding_client.embedding_size

        created = await self.vectordb_client.create_collection(
            collection_name=collection, embedding_size=size, reset=reset and asset_id is None
        )
        if asset_id is not None and not created:
            await self.vectordb_client.delete_by_metadata(collection_name=collection, key="asset_id", value=asset_id)

        by_asset: dict[str, list] = defaultdict(list)
        async for chunk in chunk_model.iter_project_chunks(project_object_id, asset_id):
            if (chunk.chunk_metadata or {}).get("kind") == "image":
                by_asset[chunk.asset_id].append(chunk)

        indexed = skipped = 0
        for owner, chunks in by_asset.items():
            asset = await asset_model.get_asset(owner)
            if asset is None or not asset.file_bytes:
                skipped += len(chunks)
                continue

            for start in range(0, len(chunks), EMBED_BATCH):
                batch = chunks[start : start + EMBED_BATCH]
                pictures, kept = [], []
                for chunk in batch:
                    try:
                        pictures.append(self._render(asset, chunk))
                        kept.append(chunk)
                    except (InvalidImageError, Exception) as exc:  # noqa: BLE001 - one bad figure is one skip
                        self.logger.warning("Could not render image chunk %s:%s: %s", owner, chunk.chunk_order, exc)
                        skipped += 1

                if not kept:
                    continue

                vectors = await self.embedding_client.embed_images(pictures)
                await self.vectordb_client.insert_many(
                    collection_name=collection,
                    texts=[chunk.chunk_content for chunk in kept],
                    vectors=vectors,
                    metadata=[
                        {
                            "project_id": str(chunk.project_id),
                            "asset_id": chunk.asset_id,
                            "chunk_order": chunk.chunk_order,
                            "source": (chunk.chunk_metadata or {}).get("source"),
                            "kind": "image",
                        }
                        for chunk in kept
                    ],
                    record_ids=[f"{chunk.asset_id}:{chunk.chunk_order}" for chunk in kept],
                )
                indexed += len(kept)

        if indexed:
            await self.vectordb_client.create_index(collection_name=collection, embedding_size=size)

        self.logger.info(
            "Indexed %d image(s) for project %r into %r (%d skipped)", indexed, project_id, collection, skipped
        )
        return {"collection": collection, "images_indexed": indexed, "images_skipped": skipped}

    def _render(self, asset, chunk) -> bytes:
        data, _ = render_chunk(asset, chunk.chunk_metadata, self.settings.IMAGE_MAX_SIDE_PX)
        return data

    # --- reading --------------------------------------------------------------

    async def search(
        self,
        project_id: str,
        *,
        text: str | None = None,
        image: bytes | None = None,
        limit: int | None = None,
        asset_ids: list[str] | None = None,
    ) -> list[dict]:
        """Nearest pictures to a typed question or to a picture, best first.

        [] when the notebook has no images: unlike the text index, a notebook
        without pictures is the normal case, not a missed indexing step.
        """
        collection = self.collection_name(project_id)
        if not await self.vectordb_client.collection_exists(collection):
            return []

        if image is not None:
            vector = (await self.embedding_client.embed_images([image]))[0]
        elif text:
            vector = (await self.embedding_client.embed_texts([text], EmbeddingInputType.QUERY))[0]
        else:
            return []

        hits = await self.vectordb_client.search_by_vector(
            collection_name=collection,
            vector=vector,
            limit=limit or self.settings.IMAGE_TOP_K,
            asset_ids=asset_ids,
        )

        floor = self.settings.IMAGE_MIN_SCORE
        for hit in hits:
            hit.setdefault("metadata", {})["kind"] = "image"
        return [hit for hit in hits if not floor or (hit.get("score") or 0.0) >= floor]

    # --- housekeeping -----------------------------------------------------------

    async def delete_asset(self, project_id: str, asset_id: str) -> int:
        collection = self.collection_name(project_id)
        if not await self.vectordb_client.collection_exists(collection):
            return 0
        return await self.vectordb_client.delete_by_metadata(collection_name=collection, key="asset_id", value=asset_id)

    async def drop(self, project_id: str) -> None:
        collection = self.collection_name(project_id)
        if await self.vectordb_client.collection_exists(collection):
            await self.vectordb_client.delete_collection(collection)
