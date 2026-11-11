"""Follow the figure <-> passage links ChunkLinker recorded at ingest.

Given the hits retrieval returned, this fetches their partner chunks: the
figure a passage refers to, the passage that explains a figure. The partners
come back shaped like hits, with ``score: None`` and ``linked_from`` naming
the hit they were brought in by, so ChatService can append, number and cite
them like anything else, and the UI can say why a figure appeared.

Never raises. The links are an improvement on an answer, not a dependency
of one; a database hiccup here costs the extras and nothing more.
"""

from __future__ import annotations

from .citations import chunks_for_keys, keys_from_hits


def _hit_from_chunk(chunk, source_hit: dict) -> dict:
    metadata = chunk.chunk_metadata or {}
    return {
        "id": f"{chunk.asset_id}:{chunk.chunk_order}",
        "score": None,
        "text": chunk.chunk_content,
        "metadata": {
            "asset_id": chunk.asset_id,
            "chunk_order": chunk.chunk_order,
            "source": metadata.get("source") or (source_hit.get("metadata") or {}).get("source"),
            "kind": metadata.get("kind") or "text",
            "project_id": (source_hit.get("metadata") or {}).get("project_id"),
        },
        "linked_from": source_hit.get("id"),
    }


async def expand_links(db, hits: list[dict], max_images: int = 2, max_text: int = 2) -> list[dict]:
    """The partner chunks of *hits* that are not already among them.

    Images first (a passage's figure), then passages (a figure's text),
    each capped. Order follows the hits, so the partners of the best hit
    come first.
    """
    keys = keys_from_hits(hits)
    if not keys:
        return []

    present = set(keys)

    # 1. The hits' own rows, for their link fields.
    rows = await chunks_for_keys(db, keys, what="read links")

    # 2. Which partners to fetch, in hit order, deduplicated.
    wanted_images: list[tuple[str, int, dict]] = []
    wanted_text: list[tuple[str, int, dict]] = []
    seen: set[tuple[str, int]] = set(present)
    for hit in hits:
        metadata = hit.get("metadata") or {}
        asset_id, order = metadata.get("asset_id"), metadata.get("chunk_order")
        chunk = rows.get((asset_id, order))
        if chunk is None:
            continue
        links = chunk.chunk_metadata or {}
        for partner in links.get("linked_images") or []:
            if (asset_id, partner) not in seen:
                seen.add((asset_id, partner))
                wanted_images.append((asset_id, partner, hit))
        for partner in links.get("linked_chunks") or []:
            if (asset_id, partner) not in seen:
                seen.add((asset_id, partner))
                wanted_text.append((asset_id, partner, hit))

    wanted = wanted_images[:max_images] + wanted_text[:max_text]
    if not wanted:
        return []

    # 3. Fetch the partners.
    partners = await chunks_for_keys(
        db, [(asset_id, order) for asset_id, order, _ in wanted], what="load linked chunks"
    )

    out = []
    for asset_id, order, source_hit in wanted:
        chunk = partners.get((asset_id, order))
        if chunk is not None:
            out.append(_hit_from_chunk(chunk, source_hit))
    return out
