"""Word coordinates for a PDF page, and the highlight rectangles a chunk maps to.

Ingest-only. A citation already knows its page from ``chunk_metadata`` (see
application/services/rag/citations.py); this is what lets it also draw a rectangle over the
cited passage rather than only opening the right page.

Deliberately separate from ProcessService's ordinary loaders. Those
(PyPDFLoader / PDFPlumberLoader / PyMuPDFLoader) all wrap a library to extract
*text* — none of them, pymupdf's own included, exposes per-word bounding
boxes. Getting those means calling pymupdf's own ``page.get_text("words")``
directly, which is what this module does and the reason it exists apart from
``ProcessService._pdf_loader``.

Only reachable when ``PDF_LOADER=pymupdf`` — see ProcessService. Measured
at ~1.9s/page serial (520s for a 274-page document) against ~0.1s/page for
plain pypdf text extraction. Pages are independent of one another — nothing
about extracting one depends on any other having run — so extract_pages
splits the document across a process pool, cutting that to ~79s at 24 workers
on the machine this was built on. Well short of a 24x speedup — see
extract_pages for why — but still the difference between minutes and over
a quarter of an hour.
"""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf  # ty: ignore[unresolved-import]

from shared.utils import cpu_count, is_daemonic

from .TextProcessingService import normalize_text, strip_nulls

# Above this many rectangles a "highlight" stops being one — a scanned table
# or a page with no real line breaks would otherwise store something too
# large to read or to make sense of. Dropped rather than truncated: a
# half-shown highlight looks like a bug, an absent one just looks unhandled.
MAX_RECTS = 120


@dataclass
class PageWords:
    """One page's word boxes, and the exact text built from them.

    ``text`` is what the splitter actually receives for this page — built
    from the same words ``starts``/``words`` index, so a chunk's
    ``start_index`` (see TextProcessingService.get_splitter) always lands
    on a word boundary here. Coordinates are pymupdf's own: top-left origin,
    y growing downward — the same convention CSS uses, so nothing computed
    from ``boxes`` needs a coordinate flip before it can be drawn.
    """

    page_index: int  # 0-based, matches chunk_metadata["page"] elsewhere
    page_label: str
    width: float
    height: float
    text: str
    starts: list[int] = field(default_factory=list)  # one per word, offset into `text`
    words: list[str] = field(default_factory=list)  # the cleaned word itself, for its length
    # x0, y0, x1, y1, block_no, line_no, word_no — pymupdf's own "words" tuple,
    # minus the word text (kept separately, above).
    boxes: list[tuple[float, float, float, float, int, int, int]] = field(default_factory=list)


def _clean_word(word: str) -> str:
    """One pymupdf "word" token, normalised the same way sanitize() would.

    Per word, not per joined line: a word that is only a bidi control
    character or a stray NBSP normalises to nothing, and joining that in
    would leave a run of whitespace for the later collapse in
    TextProcessingService.sanitize to shrink — shifting every offset after
    it out from under the boxes that were computed for the *pre-collapse*
    text. Dropping empties here, before the join, is what keeps `starts[i]`
    exactly right for `boxes[i]` with no reconciliation step afterward.
    """
    return normalize_text(strip_nulls(word)).strip()


def _extract_one_page(doc: "pymupdf.Document", page_index: int) -> PageWords:
    page = doc[page_index]
    page_words = PageWords(
        page_index=page_index,
        page_label=page.get_label() or str(page_index + 1),
        width=round(page.rect.width, 1),
        height=round(page.rect.height, 1),
        text="",
    )

    cursor = 0
    # Never sort=True — it orders by (y, x), which reverses right-to-left
    # reading order exactly the way pdfplumber's x-position ordering already
    # does wrong (see PDF_LOADER in utils/config.py). Left in pymupdf's
    # natural extraction order instead.
    for x0, y0, x1, y1, word, block_no, line_no, word_no in page.get_text("words"):
        cleaned = _clean_word(word)
        if not cleaned:
            continue

        page_words.starts.append(cursor)
        page_words.words.append(cleaned)
        page_words.boxes.append((x0, y0, x1, y1, block_no, line_no, word_no))
        cursor += len(cleaned) + 1  # the space " ".join below adds

    page_words.text = " ".join(page_words.words)
    return page_words


def _extract_page_range(file_path: str, start: int, end: int) -> list[PageWords]:
    """One worker's slice of the document — its own pymupdf.Document.

    A ``Document``/``Page`` cannot cross a process boundary (it wraps a C
    pointer into MuPDF, not plain data), so each worker reopens the file
    rather than being handed one already open. That cost is paid once per
    worker, not once per page: a *range* is dispatched, not one task per page,
    specifically so a 274-page document opens the file a handful of times
    (one per worker) rather than 274.
    """
    doc = pymupdf.open(file_path)
    try:
        return [_extract_one_page(doc, i) for i in range(start, end)]
    finally:
        doc.close()


def _page_ranges(total: int, workers: int) -> list[tuple[int, int]]:
    """*workers* contiguous, near-equal slices of ``range(total)``."""
    size = -(-total // workers)  # ceil division
    return [(i, min(i + size, total)) for i in range(0, total, size)]


def extract_pages(file_path: Path, *, max_workers: int | None = None) -> list[PageWords]:
    """Every page's word boxes and the text built from them.

    Pages do not depend on one another, so this is split across a process
    pool rather than walked serially — threads would not help here even if
    the GIL were the only concern: pymupdf's C calls do not release it for
    long enough to matter. Measured on the 274-page document this module's
    docstring cites: ~520s serial, ~79s at 24 workers on the machine this was
    built on — a ~6.6x speedup, well short of 24x. The gap is Amdahl's law,
    not a bug: the slowest handful of pages (dense tables, small print) still
    cost what they cost, splitting no finer than "one page, one task" puts a
    floor under the whole run, and 24 processes each opening their own copy
    of the file plus shipping every word and box back through IPC is not
    free either.

    A small document (or a single-core box) skips the pool entirely — process
    startup is not free, and is not worth paying for a handful of pages.
    """
    path = str(file_path)

    doc = pymupdf.open(path)
    total = doc.page_count
    doc.close()  # only needed the page count; each worker opens its own handle

    if total == 0:
        return []

    workers = min(max_workers or cpu_count(), total)

    # A Celery prefork worker is a daemonic process, and a daemonic process is
    # not allowed to have children: ProcessPoolExecutor raises
    # "daemonic processes are not allowed to have children" the moment it tries
    # to start one. Ingestion moved onto Celery, so this path now runs inside
    # exactly such a process, and every PDF upload failed with an
    # ExtractionError that named the temp file and not the cause.
    #
    # Serial rather than a thread pool, for the reason in the docstring above:
    # pymupdf's C calls do not release the GIL for long enough to make threads
    # worth the complexity. The concurrency that matters here is across
    # documents — which the worker pool already provides — not within one.
    if workers > 1 and is_daemonic():
        workers = 1

    if workers <= 1:
        doc = pymupdf.open(path)
        try:
            return [_extract_one_page(doc, i) for i in range(total)]
        finally:
            doc.close()

    ranges = _page_ranges(total, workers)

    with ProcessPoolExecutor(max_workers=len(ranges)) as pool:
        chunks = pool.map(
            _extract_page_range,
            [path] * len(ranges),
            [r[0] for r in ranges],
            [r[1] for r in ranges],
        )

        pages: list[PageWords] = []
        for chunk in chunks:
            pages.extend(chunk)

    return pages


def page_count(file_path: Path) -> int:
    """How many pages the document has, without extracting any of them.

    Opening the file and closing it again is the whole cost — the planner that
    decides how many batches to fan out needs the count and nothing else, and
    reading a 274-page document to learn it is 520s spent to answer a question
    the header already knows.
    """
    doc = pymupdf.open(str(file_path))
    try:
        return doc.page_count
    finally:
        doc.close()


def extract_page_range(file_path: Path, start: int, end: int) -> list[PageWords]:
    """The word boxes for pages ``[start, end)``.

    The public form of `_extract_page_range`, which existed only as a
    process-pool worker. It is called directly now by the parse stage, where
    the slicing is done by Celery across workers rather than by a pool inside
    one — which is also what gets that parallelism back in production, since
    the pool in `extract_pages` degrades to serial inside a daemonic prefork
    worker (see the note there).
    """
    return _extract_page_range(str(file_path), start, end)


def page_to_dict(page: PageWords) -> dict:
    """A PageWords as JSON-safe plain data, for the trip through the broker.

    Celery's serializer is JSON, so the dataclass cannot travel as itself and
    the box tuples arrive as lists. Written out explicitly rather than via
    `dataclasses.asdict` so that adding a field to PageWords is a decision
    about what crosses the wire -- these payloads carry every word of every
    page and are the largest thing this pipeline puts in the result backend.
    """
    return {
        "page_index": page.page_index,
        "page_label": page.page_label,
        "width": page.width,
        "height": page.height,
        "text": page.text,
        "starts": page.starts,
        "words": page.words,
        "boxes": page.boxes,
    }


def page_from_dict(data: dict) -> PageWords:
    """Rebuild a PageWords from `page_to_dict`.

    The boxes are returned to tuples: `rects_for_range` unpacks them
    positionally and compares line/block numbers, which works on a list too,
    but keeping the type identical to the extractor's output means nothing
    downstream can start depending on the difference.
    """
    return PageWords(
        page_index=data["page_index"],
        page_label=data["page_label"],
        width=data["width"],
        height=data["height"],
        text=data["text"],
        starts=list(data["starts"]),
        words=list(data["words"]),
        boxes=[tuple(box) for box in data["boxes"]],
    )


def _word_end(page: PageWords, index: int) -> int:
    return page.starts[index] + len(page.words[index])


def _same_line(a: tuple, b: tuple) -> bool:
    """True when box *b* continues box *a*'s line with no gap between them.

    Same (block, line) is not quite enough on its own: a chunk boundary that
    falls mid-line, or a line pymupdf reports with a real skip in it, should
    not be merged into one rectangle spanning words that were never selected
    together. Requiring the word_no to be exactly consecutive catches both.
    """
    return a[4] == b[4] and a[5] == b[5] and b[6] == a[6] + 1


def rects_for_range(page: PageWords, start: int, end: int) -> list[list[float]]:
    """Line-level highlight rectangles covering ``page.text[start:end]``.

    Selects every word overlapping the range, groups contiguous same-line
    runs into one rectangle each, and caps the result — see MAX_RECTS. Empty
    input, an empty selection, or a page that trips the cap all return ``[]``;
    the caller stores no highlight at all rather than a degenerate one.
    """
    selected = [i for i in range(len(page.starts)) if page.starts[i] < end and _word_end(page, i) > start]
    if not selected:
        return []

    groups: list[list[int]] = [[selected[0]]]
    for prev, cur in zip(selected, selected[1:]):
        if _same_line(page.boxes[prev], page.boxes[cur]):
            groups[-1].append(cur)
        else:
            groups.append([cur])

    if len(groups) > MAX_RECTS:
        return []

    rects = []
    for group in groups:
        boxes = [page.boxes[i] for i in group]
        rects.append(
            [
                round(min(b[0] for b in boxes), 1),
                round(min(b[1] for b in boxes), 1),
                round(max(b[2] for b in boxes), 1),
                round(max(b[3] for b in boxes), 1),
            ]
        )

    return rects


def highlight_metadata(page: PageWords, start: int, end: int, scale: float = 1.0) -> dict | None:
    """The full ``chunk_metadata["highlight"]`` value, or None if unavailable.

    ``v`` is a schema version: the one field that lets a future coordinate
    fix invalidate old rows without reading every one of them to check.

    ``scale`` maps offsets from a *different* rendering of this page onto the
    word boxes, and exists for one case: the chunk's text came from OCR because
    the page's own text layer was unusable, so its offsets index a string that
    is not ``page.text``. The boxes are still the best positional information
    available — OCR produces none — and both strings describe the same page in
    the same reading order, so a proportional map lands in the right region.

    It is an approximation, and says so: the result carries ``"approx": 1`` so a
    reader can tell a highlight that is exact from one that is merely close, and
    so a future change can find them. With fixed-size chunks covering a good
    fraction of a page, close is useful; character-exact was never available
    once the text was re-read.

    A page with no word boxes at all but a known size is highlighted whole.
    That is a page OCR read because it had no usable text layer (see
    `ProcessService.ocr_unreadable_pages`): its text is real, but nothing
    says where on the paper any of it sits, so the honest answer is "this
    page" rather than no highlight. It is marked ``approx`` like the scaled
    case. A page from a loader with no geometry has width and height 0 and
    still gets nothing.
    """
    if not page.boxes and page.width > 0 and page.height > 0:
        return {
            "v": 1,
            "w": page.width,
            "h": page.height,
            "o": "tl",
            "r": [[0.0, 0.0, round(page.width, 1), round(page.height, 1)]],
            "approx": 1,
        }

    if scale != 1.0:
        start, end = int(start * scale), int(end * scale)

    rects = rects_for_range(page, start, end)
    if not rects:
        return None

    highlight = {"v": 1, "w": page.width, "h": page.height, "o": "tl", "r": rects}

    if scale != 1.0:
        highlight["approx"] = 1

    return highlight
