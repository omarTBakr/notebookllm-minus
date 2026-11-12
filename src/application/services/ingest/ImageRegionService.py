"""Where the pictures are in a document, and the pixels of one on demand.

Two halves of one idea: an image is never stored twice. At ingest
:func:`find_figures` records *where* each figure sits (page, bounding box,
the PDF's own image xref); the region becomes an image chunk whose highlight
is that box. When an image is needed later (to embed it, caption it, show it
in a citation or hand it to a vision model) :func:`render_region` cuts it
back out of the asset's stored bytes. The asset is already in the database;
a second copy of every figure would double the storage for no new
information.

Coordinates are PyMuPDF points with a top-left origin, the same space the
word boxes and every citation highlight use, so an image chunk outlines its
figure in the PDF viewer with no new code there.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf  # ty: ignore[unresolved-import]

from shared.enums import AssetType
from shared.utils import get_logger
from shared.utils.images import prepare

logger = get_logger(__name__)

#: Resolution figures are cut out at. 150 dpi keeps small print in a chart
#: legible after the IMAGE_MAX_SIDE_PX downscale without rendering posters.
CROP_DPI = 150


@dataclass(frozen=True)
class FigureRules:
    min_side_px: int = 96
    min_area_ratio: float = 0.02
    max_repeat: int = 3

    @classmethod
    def from_settings(cls, settings) -> "FigureRules":
        return cls(
            min_side_px=settings.IMAGE_MIN_SIDE_PX,
            min_area_ratio=settings.IMAGE_MIN_AREA_RATIO,
            max_repeat=settings.IMAGE_MAX_REPEAT,
        )


def _repeat_counts(doc) -> Counter:
    """How many pages each image xref appears on, across the whole document.

    A batch sees only its own pages, but a logo is only recognisable as a logo
    across all of them. Listing a page's image references reads the page's
    resource dictionary, not the pixels, so this costs milliseconds even on a
    few hundred pages.
    """
    counts: Counter = Counter()
    for index in range(doc.page_count):
        counts.update({info[0] for info in doc.get_page_images(index)})
    return counts


def _round_box(rect) -> list[float]:
    return [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]


def find_figures(path: Path | str, start: int, end: int, rules: FigureRules = FigureRules()) -> dict[int, list[dict]]:
    """The figures on pages ``[start, end)``, keyed by 0-based page index.

    Each region is ``{"bbox": [x0, y0, x1, y1], "xref": int}``. Left out:

    - pictures smaller than ``min_side_px`` on either side, or covering less
      than ``min_area_ratio`` of the page (icons, bullets, rules);
    - pictures whose xref appears on more than ``max_repeat`` pages (logos,
      letterheads, page-border art);
    - a second placement of the same xref at the same spot on one page.
    """
    figures: dict[int, list[dict]] = {}

    with pymupdf.open(str(path)) as doc:
        repeats = _repeat_counts(doc)
        stop = min(end, doc.page_count)

        for index in range(start, stop):
            page = doc[index]
            page_area = max(page.rect.width * page.rect.height, 1.0)
            seen: set[tuple] = set()
            regions: list[dict] = []

            for info in page.get_image_info(xrefs=True):
                xref = int(info.get("xref") or 0)
                width, height = info.get("width", 0), info.get("height", 0)
                rect = pymupdf.Rect(info["bbox"]) & page.rect

                if rect.is_empty:
                    continue
                if min(width, height) < rules.min_side_px:
                    continue
                if (rect.width * rect.height) / page_area < rules.min_area_ratio:
                    continue
                if xref and repeats[xref] > rules.max_repeat:
                    continue

                key = (xref, *_round_box(rect))
                if key in seen:
                    continue
                seen.add(key)

                regions.append({"bbox": _round_box(rect), "xref": xref or None})

            if regions:
                figures[index] = regions

    return figures


def page_has_marks(path: Path | str, index: int) -> bool:
    """Whether page *index* carries anything visible: a picture or drawing.

    Separates a picture-only page (a scan, a full-page diagram) that is worth
    keeping as an image from a page that is simply blank.
    """
    with pymupdf.open(str(path)) as doc:
        if index >= doc.page_count:
            return False
        page = doc[index]
        return bool(page.get_images(full=False)) or bool(page.get_drawings())


def whole_page_region(width: float, height: float) -> dict:
    """A region covering a page of *width* x *height* points."""
    return {"bbox": [0.0, 0.0, round(width, 2), round(height, 2)], "xref": None, "whole_page": True}


# --- rendering -------------------------------------------------------------


def _render_pdf(data: bytes, page_index: int, bbox: list[float]) -> bytes:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        page = doc[page_index]
        clip = pymupdf.Rect(bbox) & page.rect
        pixmap = page.get_pixmap(clip=clip, dpi=CROP_DPI)
        return pixmap.tobytes("png")


@lru_cache(maxsize=64)
def _render_cached(
    content_hash: str, page_index: int, bbox: tuple, max_side: int, data_ref: "_Bytes"
) -> tuple[bytes, str]:
    if data_ref.is_pdf:
        raw = _render_pdf(data_ref.data, page_index, list(bbox))
    else:
        raw = data_ref.data
    prepared = prepare(raw, max_side=max_side)
    return prepared.data, prepared.mime


class _Bytes:
    """Carries the asset bytes into the cache without making them the key.

    lru_cache hashes every argument; hashing a 10 MB blob on every lookup
    would cost more than the render it saves. Equality is by content hash,
    which is already part of the key.
    """

    __slots__ = ("data", "is_pdf", "_hash")

    def __init__(self, data: bytes, is_pdf: bool, content_hash: str) -> None:
        self.data = data
        self.is_pdf = is_pdf
        self._hash = content_hash

    def __hash__(self) -> int:
        return hash(self._hash)

    def __eq__(self, other) -> bool:
        return isinstance(other, _Bytes) and other._hash == self._hash


def render_region(
    data: bytes,
    *,
    is_pdf: bool,
    content_hash: str,
    page_index: int,
    bbox: list[float] | None,
    max_side: int = 1024,
) -> tuple[bytes, str]:
    """The image a region shows, as ``(bytes, mime)``, scaled to *max_side*.

    For a PDF the region is cut out of its page; for an uploaded image the
    asset *is* the image and *bbox* is ignored.
    """
    box = tuple(bbox or ())
    return _render_cached(content_hash, page_index, box, max_side, _Bytes(data, is_pdf, content_hash))


def render_asset_region(asset, page_index: int, bbox: list[float] | None, max_side: int = 1024) -> tuple[bytes, str]:
    """`render_region` for a stored asset: its bytes, its kind and its cache key.

    The one place that knows an uploaded picture *is* its only region while
    anything else is cut out of a PDF page, and that crops are cached under
    the asset's content hash (so a re-upload of the same file reuses them).
    """
    return render_region(
        asset.file_bytes,
        is_pdf=asset.asset_type != AssetType.IMAGE,
        content_hash=asset.content_hash or asset.asset_id,
        page_index=page_index,
        bbox=bbox,
        max_side=max_side,
    )


def render_chunk(asset, chunk_metadata: dict | None, max_side: int = 1024) -> tuple[bytes, str]:
    """The picture an image chunk shows, from the page and box its metadata records."""
    metadata = chunk_metadata or {}
    return render_asset_region(asset, int(metadata.get("page", 0)), metadata.get("bbox"), max_side)
