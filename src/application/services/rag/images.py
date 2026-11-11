"""The pixels behind image chunks, cut back out of their assets on demand.

Used twice: to hand a vision model the figures an answer cites, and to serve
a citation's thumbnail. In both cases the chunk only records *where* the
picture is (page and bounding box); the bytes come from the stored asset,
rendered by ImageRegionService.render_region, which caches recent crops.
"""

from __future__ import annotations

from collections import defaultdict

from data.models import AssetModel, ChunkModel
from shared.enums import AssetType
from shared.utils import get_logger, get_settings

from ..ingest.ImageRegionService import render_region
from .citations import chunks_for_keys, keys_from_hits

logger = get_logger(__name__)

ImageMap = dict[tuple[str, int], tuple[bytes, str]]


def _render(asset, chunk, max_side: int) -> tuple[bytes, str]:
    metadata = chunk.chunk_metadata or {}
    return render_region(
        asset.file_bytes,
        is_pdf=asset.asset_type != AssetType.IMAGE,
        content_hash=asset.content_hash or asset.asset_id,
        page_index=int(metadata.get("page", 0)),
        bbox=metadata.get("bbox"),
        max_side=max_side,
    )


async def load_hit_images(db, hits: list[dict], max_side: int | None = None) -> ImageMap:
    """``{(asset_id, chunk_order): (bytes, mime)}`` for the image chunks among *hits*.

    Never raises: a picture that cannot be produced is simply left out, and
    the model reads that hit's caption instead.
    """
    max_side = max_side or get_settings().IMAGE_MAX_SIDE_PX

    rows = await chunks_for_keys(db, keys_from_hits(hits), what="load pictures")

    # Only the image chunks need their asset's bytes, so a text-only answer
    # reads no asset at all.
    by_asset: dict[str, list[tuple[int, object]]] = defaultdict(list)
    for (asset_id, order), chunk in rows.items():
        if (chunk.chunk_metadata or {}).get("kind") == "image":
            by_asset[asset_id].append((order, chunk))

    assets_model = AssetModel(db)
    images: ImageMap = {}

    for asset_id, chunks in by_asset.items():
        try:
            asset = await assets_model.get_asset(asset_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load pictures for asset %r: %s", asset_id, exc)
            continue

        if asset is None or not asset.file_bytes:
            continue

        for order, chunk in chunks:
            try:
                images[(asset_id, order)] = _render(asset, chunk, max_side)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not render picture %s:%s: %s", asset_id, order, exc)

    return images


async def load_chunk_image(db, asset, chunk_order: int, max_side: int | None = None) -> tuple[bytes, str] | None:
    """One image chunk's picture, or None when the chunk is not an image."""
    chunks = await ChunkModel(db).get_chunks_by_orders(asset.asset_id, [chunk_order])
    chunk = chunks.get(chunk_order)
    if chunk is None or (chunk.chunk_metadata or {}).get("kind") != "image":
        return None
    return _render(asset, chunk, max_side or get_settings().IMAGE_MAX_SIDE_PX)
