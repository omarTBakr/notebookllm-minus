"""A short text description of every picture, written once at ingest.

The caption is what makes an image a first-class search result rather than a
side channel:

- the reranker scores it against the question exactly like a text passage,
  which puts image hits and text hits on one comparable scale;
- a chat model without vision reads it in place of the picture;
- it is indexed in the notebook's text collection too, so a plain text
  search ("revenue by quarter") finds the chart whose caption says so.

A vision chat model (IMAGE_CAPTION_MODEL_ID) writes it. When none is set, or
the call fails, :func:`nearby_text` stands in: the lines printed closest to
the picture, which in a report or a paper is very often its own
"Figure 3: ..." caption. Neither path ever fails an ingest.
"""

from __future__ import annotations

import asyncio
import re

from application.prompts.template_parser import TemplateParser
from shared.utils import get_logger, tracing
from shared.utils.images import InvalidImageError

logger = get_logger(__name__)

#: How far (in points) above or below a figure a line may sit and still be
#: read as belonging to it. About four lines of body text.
NEARBY_MAX_GAP = 60.0

#: Lines that announce a caption, in either language, win over plain
#: proximity. "شكل" / "الشكل" is figure, "جدول" table, "صورة" picture.
_CAPTION_START = re.compile(r"^\s*(fig(ure)?\.?|table|chart|diagram|plate|map|الشكل|شكل|جدول|صورة|رسم)\b", re.I)

#: How many captions one ingest asks the model for at once. The model is a
#: local GPU; parallel requests only queue inside Ollama.
CAPTION_CONCURRENCY = 2

MAX_CAPTION_CHARS = 1200


def _lines(page: dict) -> list[tuple[float, float, float, float, str]]:
    """The page's words grouped into lines: (x0, y0, x1, y1, text)."""
    grouped: dict[tuple[int, int], list] = {}
    for word, box in zip(page.get("words") or [], page.get("boxes") or []):
        x0, y0, x1, y1, block, line = box[:6]
        grouped.setdefault((int(block), int(line)), []).append((x0, y0, x1, y1, word))

    lines = []
    for words in grouped.values():
        text = " ".join(w[4] for w in words).strip()
        if text:
            lines.append(
                (
                    min(w[0] for w in words),
                    min(w[1] for w in words),
                    max(w[2] for w in words),
                    max(w[3] for w in words),
                    text,
                )
            )
    return lines


def nearby_text(page: dict, bbox: list[float], max_chars: int = 300) -> str:
    """The text printed closest to *bbox* on *page*, or "".

    A line that starts like a caption ("Figure 3", "الشكل ٣") within reach
    wins outright, together with the line after it. Otherwise the closest
    line below the figure (where captions usually sit), then above.
    """
    lines = _lines(page)
    if not lines:
        return ""

    x0, y0, x1, y1 = bbox

    def overlaps_horizontally(line) -> bool:
        return line[2] >= x0 - 20 and line[0] <= x1 + 20

    def gap(line) -> float:
        if line[1] >= y1:  # below
            return line[1] - y1
        if line[3] <= y0:  # above
            return y0 - line[3] + 0.5  # ties go to the line below
        return float("inf")  # inside the figure: labels, already in the picture

    candidates = sorted(
        (line for line in lines if overlaps_horizontally(line) and gap(line) <= NEARBY_MAX_GAP),
        key=gap,
    )
    if not candidates:
        return ""

    for index, line in enumerate(candidates):
        if _CAPTION_START.match(line[4]):
            following = [c for c in candidates[index + 1 :] if c[1] > line[1]][:1]
            return " ".join([line[4], *(c[4] for c in following)])[:max_chars]

    return candidates[0][4][:max_chars]


def annotate_nearby(pages: list[dict]) -> None:
    """Record the text printed next to every region as ``region["nearby"]``.

    Kept on the region, and later on the image chunk, because it is the most
    reliable place to read a figure's own label ("Figure 3", "الشكل ٣") from:
    a model's caption may paraphrase it away, and the linker (ChunkLinker)
    matches passages to figures by that label.
    """
    for page in pages:
        for region in page.get("images") or []:
            if "nearby" in region:
                continue
            region["nearby"] = "" if region.get("whole_page") else nearby_text(page, region["bbox"])


def fallback_caption(page: dict, region: dict) -> str:
    """The caption used when no model wrote one."""
    near = nearby_text(page, region["bbox"]) if not region.get("whole_page") else ""
    label = page.get("page_label") or str(page["page_index"] + 1)
    return near or f"[image, page {label}]"


class ImageCaptionService:
    """Writes captions for the image regions of a batch, in place."""

    def __init__(self, client, render, lang: str = "en", max_side: int = 1024) -> None:
        """*render(page, region) -> (bytes, mime)* cuts a region's picture out."""
        self.client = client
        self.render = render
        self.lang = lang
        self.max_side = max_side
        self.parser = TemplateParser(lang=lang)

    def _prompt(self, nearby: str) -> tuple[str, object | None]:
        context = self.parser.get("caption", "caption_context", {"nearby": nearby}) if nearby else ""
        return self.parser.get_with_ref(
            "caption",
            "caption_prompt",
            {"context": context, "language": self.parser.get("caption", "language_name")},
        )

    async def _one(self, page: dict, region: dict) -> str | None:
        try:
            data, mime = self.render(page, region)
        except (InvalidImageError, Exception) as exc:  # noqa: BLE001
            logger.warning("Could not render a figure on page %d: %s", page["page_index"] + 1, exc)
            return None

        nearby = "" if region.get("whole_page") else nearby_text(page, region["bbox"])
        prompt, ref = self._prompt(nearby)

        try:
            with tracing.linked_prompt(ref):
                text = await self.client.generate_text(prompt, images=[(data, mime)], max_tokens=300, temperature=0.1)
        except Exception as exc:  # noqa: BLE001 - a caption is never worth failing an ingest
            logger.warning("Caption model failed on page %d: %s", page["page_index"] + 1, exc)
            return None

        text = " ".join(text.split())
        return text[:MAX_CAPTION_CHARS] or None

    async def caption(self, pages: list[dict]) -> int:
        """Fill ``region["caption"]`` for every region on *pages*. Returns how many the model wrote."""
        jobs = [(page, region) for page in pages for region in page.get("images") or []]
        if not jobs:
            return 0

        gate = asyncio.Semaphore(CAPTION_CONCURRENCY)

        async def run(page, region):
            async with gate:
                return await self._one(page, region)

        results = await asyncio.gather(*(run(page, region) for page, region in jobs))

        written = 0
        for (page, region), text in zip(jobs, results):
            if text:
                region["caption"] = text
                written += 1
            else:
                region["caption"] = fallback_caption(page, region)

        return written


async def caption_picture(client, data: bytes, mime: str, lang: str = "en") -> str | None:
    """A caption for one standalone picture (one attached to a question), or None.

    Same prompt as a document figure, with no surrounding text to lean on.
    """
    parser = TemplateParser(lang=lang)
    prompt, ref = parser.get_with_ref(
        "caption",
        "caption_prompt",
        {"context": "", "language": parser.get("caption", "language_name")},
    )
    try:
        with tracing.linked_prompt(ref):
            text = await client.generate_text(prompt, images=[(data, mime)], max_tokens=300, temperature=0.1)
    except Exception as exc:  # noqa: BLE001 - the question still goes ahead without it
        logger.warning("Could not caption the attached picture: %s", exc)
        return None
    text = " ".join(text.split())
    return text[:MAX_CAPTION_CHARS] or None
