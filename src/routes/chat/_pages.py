"""Turning a search hit back into a page of the original document.

A hit knows only ``(asset_id, chunk_order)`` — that is all NLPController._flush
puts in the vector payload. The page number lives on the chunk row, in the
metadata the PDF loader stamped there at extraction. This bridges the two.

**The one place a page number changes base.** Three different things in this
codebase are called a page number and only one of them is safe to show:

  ``chunk_metadata["page"]``  0-based physical index, what every loader emits
  ``page_label``             a display *string*: "12", but also "iii", "Cover"
  the viewer / ``#page=N``   1-based physical index

So a citation never carries a key called ``page``. It carries ``page_number``
(1-based, for the viewer) and ``page_label`` (for the reader), and the ``+ 1``
happens here and nowhere else. Parsing ``page_label`` to get a number is the
mistake this shape exists to prevent: on a book with roman front matter it
opens page 3 instead of page 15, and looks right in every test whose fixture
has ``page_label == str(page + 1)``.
"""

from collections import defaultdict

from models import ChunkModel
from utils import get_logger

logger = get_logger(__name__)

# (asset_id, chunk_order) -> {"page_number": int, "page_label": str}
PageMap = dict[tuple[str, int], dict]


def located_from_metadata(metadata: dict) -> dict | None:
    """One chunk's metadata as a citation's page fields, or None."""
    page = (metadata or {}).get("page")

    # Not a PDF (a .txt note has no page), or extracted by something that did
    # not record one. Both are ordinary; the citation simply has no page.
    if page is None:
        return None

    try:
        page = int(page)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-numeric page %r in chunk metadata", page)
        return None

    # page_label is pypdf's alone — pymupdf and pdfplumber set only `page`, so
    # this cannot be read unconditionally. str(page + 1) is the right fallback
    # precisely because it is what a label would say for an unlabelled PDF.
    label = (metadata or {}).get("page_label")

    return {
        "page_number": page + 1,
        "page_label": str(label) if label not in (None, "") else str(page + 1),
    }


async def resolve_pages(db, keys: list[tuple[str, int]]) -> PageMap:
    """Page number and label for each ``(asset_id, chunk_order)``.

    Grouped by asset so one query serves every citation from the same source,
    which is the common case: an answer usually cites one document.

    Never raises. A citation that cannot be placed is still a usable citation —
    it just does not become a link — and that is a far better outcome than a
    database hiccup taking down the answer it belongs to.
    """
    if not keys:
        return {}

    by_asset: dict[str, list[int]] = defaultdict(list)
    for asset_id, chunk_order in keys:
        if asset_id is not None and chunk_order is not None:
            by_asset[asset_id].append(chunk_order)

    if not by_asset:
        return {}

    chunk_model = ChunkModel(db)
    pages: PageMap = {}

    for asset_id, orders in by_asset.items():
        try:
            chunks = await chunk_model.get_chunks_by_orders(asset_id, orders)
        except Exception as exc:
            logger.warning("Could not resolve pages for asset %r: %s", asset_id, exc)
            continue

        for order, chunk in chunks.items():
            located = located_from_metadata(chunk.chunk_metadata)
            if located is not None:
                pages[(asset_id, order)] = located

    return pages


def keys_from_hits(hits: list[dict]) -> list[tuple[str, int]]:
    """The ``(asset_id, chunk_order)`` pairs to look up for these search hits."""
    keys = []

    for hit in hits:
        metadata = hit.get("metadata") or {}
        asset_id = metadata.get("asset_id")
        chunk_order = metadata.get("chunk_order")

        # `is not None`, not truthiness: chunk_order 0 is the first chunk of
        # every document and is falsy.
        if asset_id is not None and chunk_order is not None:
            keys.append((asset_id, chunk_order))

    return keys
