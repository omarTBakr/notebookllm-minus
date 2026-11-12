"""Which passages a figure belongs to, and which figures a passage refers to.

Pictures and text are chunked separately (ImageRegionService, assemble.py),
so without this nothing ties "as Figure 3 shows, revenue rose" to Figure 3.
This records the tie at ingest, on both chunks' metadata:

- an image chunk gets ``linked_chunks``: the text chunks that refer to it or
  sit next to it, best first;
- a text chunk gets ``linked_images``: the figures it refers to or sits by.

At answer time ``rag/links.py`` follows them, so a retrieved passage brings
its figure and a retrieved figure brings its explanation.

Two signals, in order of strength:

1. **An explicit reference.** The passage names the figure's label: "Figure
   3", "Fig. 3", "Table 2", "الشكل ٣", "شكل 3", "جدول ٢". The figure's own
   label is read from the text printed next to it (``nearby_text``) or its
   caption. Links across pages; a report's figures are discussed anywhere.
2. **Proximity on the page.** For a PDF with word boxes, how far the
   passage's highlight rectangles sit above or below the figure, counted
   only when they overlap horizontally (a two-column page's other column is
   not "next to" the figure). A passage with no geometry, or an approximate
   whole-page highlight, links only by sharing the page, weakly.

Coordinates are PyMuPDF points, top-left origin, the same space as every
highlight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: "Figure 3", "Fig. 3", "fig 3a", "Table 2", "Chart 1", "الشكل ٣", "شكل 3",
#: "جدول ٢", "الجدول 2", "صورة 4". The number may be Arabic-Indic.
_LABEL = re.compile(
    r"(?:\b(fig(?:ure)?|table|chart|diagram|map|plate|exhibit)\.?\s*|(?:ال)?(شكل|جدول|صورة|رسم|مخطط)\s*)"
    r"([0-9٠-٩۰-۹]+)([a-zء-ي]?)",
    re.IGNORECASE,
)

_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_KIND_OF = {
    "fig": "figure",
    "figure": "figure",
    "شكل": "figure",
    "رسم": "figure",
    "مخطط": "figure",
    "diagram": "figure",
    "chart": "figure",
    "plate": "figure",
    "exhibit": "figure",
    "map": "figure",
    "صورة": "figure",
    "table": "table",
    "جدول": "table",
}

#: How far (points) above or below a figure a passage may sit and still be
#: "next to" it. About a third of a page.
PROXIMITY_MAX_GAP = 250.0

SCORE_REFERENCE = 10.0
SCORE_SAME_PAGE = 0.5


@dataclass(frozen=True)
class LinkRules:
    max_text_per_image: int = 2
    max_images_per_text: int = 2

    @classmethod
    def from_settings(cls, settings) -> "LinkRules":
        return cls(
            max_text_per_image=settings.LINK_MAX_TEXT_PER_IMAGE,
            max_images_per_text=settings.LINK_MAX_IMAGES_PER_TEXT,
        )


def labels_in(text: str) -> set[tuple[str, str]]:
    """The figure/table labels *text* names, as ``(kind, number)`` pairs.

    "see Fig. 3 and Table 2" -> {("figure", "3"), ("table", "2")}. The
    number keeps a trailing letter ("3a") and is normalised to ASCII digits.
    """
    found = set()
    for match in _LABEL.finditer(text or ""):
        word = (match.group(1) or match.group(2) or "").lower().rstrip(".")
        kind = _KIND_OF.get(word)
        if kind is None:
            continue
        number = match.group(3).translate(_ARABIC_INDIC) + (match.group(4) or "").lower()
        found.add((kind, number))
    return found


def _own_label(metadata: dict, caption: str) -> set[tuple[str, str]]:
    """The label a figure carries itself: from its nearby text first, else its caption."""
    near = labels_in(metadata.get("nearby_text") or "")
    if near:
        # The printed caption names exactly one figure; the first match is it.
        return {next(iter(sorted(near)))}
    own = labels_in(caption)
    return {next(iter(sorted(own)))} if own else set()


def _rects(metadata: dict) -> list[list[float]] | None:
    highlight = metadata.get("highlight") or {}
    if highlight.get("approx") or not highlight.get("r"):
        return None
    return highlight["r"]


def _proximity(text_meta: dict, bbox: list[float]) -> float:
    """0..1 for how close a passage's rectangles sit to *bbox*, 0 when not beside it."""
    rects = _rects(text_meta)
    if not rects:
        return 0.0

    x0, y0, x1, y1 = bbox
    best = None
    for rx0, ry0, rx1, ry1 in rects:
        if rx1 < x0 - 20 or rx0 > x1 + 20:
            continue  # the other column
        if ry0 >= y1:
            gap = ry0 - y1
        elif ry1 <= y0:
            gap = y0 - ry1
        else:
            gap = 0.0  # the passage flows around or through the figure's box
        best = gap if best is None else min(best, gap)

    if best is None or best > PROXIMITY_MAX_GAP:
        return 0.0
    return 1.0 - best / PROXIMITY_MAX_GAP


def score_pair(text_meta: dict, text: str, image_meta: dict, caption: str) -> float:
    """How strongly one passage and one figure belong together; 0 means not at all."""
    score = 0.0

    label = _own_label(image_meta, caption)
    if label and label & labels_in(text):
        score += SCORE_REFERENCE

    if text_meta.get("page") == image_meta.get("page"):
        near = _proximity(text_meta, image_meta.get("bbox") or [0, 0, 0, 0])
        score += SCORE_SAME_PAGE + 4.0 * near if not image_meta.get("whole_page") else SCORE_SAME_PAGE

    return score


def link_chunks(chunks: list, rules: LinkRules = LinkRules()) -> int:
    """Write ``linked_images`` / ``linked_chunks`` onto *chunks*' metadata in place.

    *chunks* are the DataChunk rows of one asset, text and image together.
    Returns how many links were recorded.
    """
    texts = [c for c in chunks if (c.chunk_metadata or {}).get("kind") != "image"]
    images = [c for c in chunks if (c.chunk_metadata or {}).get("kind") == "image"]
    if not texts or not images:
        return 0

    # Each side keeps its best partners by score.
    by_image: dict[int, list[tuple[float, int]]] = {image.chunk_order: [] for image in images}
    by_text: dict[int, list[tuple[float, int]]] = {text.chunk_order: [] for text in texts}

    for image in images:
        image_meta = image.chunk_metadata or {}
        for text in texts:
            score = score_pair(text.chunk_metadata or {}, text.chunk_content, image_meta, image.chunk_content)
            if score <= 0:
                continue
            by_image[image.chunk_order].append((score, text.chunk_order))
            by_text[text.chunk_order].append((score, image.chunk_order))

    links = 0
    for image in images:
        best = sorted(by_image[image.chunk_order], key=lambda item: (-item[0], item[1]))
        chosen = [order for _, order in best[: rules.max_text_per_image]]
        if chosen:
            image.chunk_metadata["linked_chunks"] = chosen
            links += len(chosen)
    for text in texts:
        best = sorted(by_text[text.chunk_order], key=lambda item: (-item[0], item[1]))
        chosen = [order for _, order in best[: rules.max_images_per_text]]
        if chosen:
            text.chunk_metadata["linked_images"] = chosen
            links += len(chosen)

    return links
