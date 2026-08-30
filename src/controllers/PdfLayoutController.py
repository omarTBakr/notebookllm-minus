"""Word coordinates for a PDF page, and the highlight rectangles a chunk maps to.

Ingest-only. A citation already knows its page from ``chunk_metadata`` (see
routes/chat/_pages.py); this is what lets it also draw a rectangle over the
cited passage rather than only opening the right page.

Deliberately separate from ProcessController's ordinary loaders. Those
(PyPDFLoader / PDFPlumberLoader / PyMuPDFLoader) all wrap a library to extract
*text* — none of them, pymupdf's own included, exposes per-word bounding
boxes. Getting those means calling pymupdf's own ``page.get_text("words")``
directly, which is what this module does and the reason it exists apart from
``ProcessController._pdf_loader``.

Only reachable when ``PDF_LOADER=pymupdf`` — see ProcessController. Measured
at ~1.9s/page serial (520s for a 274-page document) against ~0.1s/page for
plain pypdf text extraction. Pages are independent of one another — nothing
about extracting one depends on any other having run — so extract_pages
splits the document across a process pool, cutting that to ~79s at 24 workers
on the machine this was built on. Well short of a 24x speedup — see
extract_pages for why — but still the difference between minutes and over
a quarter of an hour.
"""

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf  # ty: ignore[unresolved-import]

from .TextProcessingController import normalize_text, strip_nulls

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
    ``start_index`` (see TextProcessingController.get_splitter) always lands
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
    boxes: list[tuple[float, float, float, float, int, int, int]] = field(
        default_factory=list
    )


def _clean_word(word: str) -> str:
    """One pymupdf "word" token, normalised the same way sanitize() would.

    Per word, not per joined line: a word that is only a bidi control
    character or a stray NBSP normalises to nothing, and joining that in
    would leave a run of whitespace for the later collapse in
    TextProcessingController.sanitize to shrink — shifting every offset after
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


# cgroup v2's unified hierarchy, and v1's fallback — checked in that order.
# Paths, not inlined into _cgroup_quota_cores, so a test can point them at a
# fixture file instead of the real (and here, absent) /sys/fs/cgroup.
_CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def _cgroup_quota_cores() -> float | None:
    """Cores this process is limited to by a cgroup CPU *quota*, or None.

    A quota (Docker's ``--cpus``, compose's ``deploy.resources.limits.cpus``)
    caps total CPU *time* across however many cores the kernel schedules
    onto — it is a different mechanism from ``--cpuset-cpus``, which pins a
    process to specific cores. Nothing about a time quota changes which cores
    are visible, so `sched_getaffinity` cannot see it and would report the
    full host regardless of it — this is the other half of the ceiling,
    checked separately, because either limit can apply without the other.
    """
    try:
        if _CGROUP_V2_CPU_MAX.is_file():
            quota, period = _CGROUP_V2_CPU_MAX.read_text().split()
            return int(quota) / int(period) if quota != "max" else None

        if _CGROUP_V1_QUOTA.is_file() and _CGROUP_V1_PERIOD.is_file():
            quota = int(_CGROUP_V1_QUOTA.read_text())
            return quota / int(_CGROUP_V1_PERIOD.read_text()) if quota > 0 else None
    except (OSError, ValueError):
        # Malformed or unreadable — same as "no quota found", not a reason to
        # fail the extraction it is only trying to size a worker pool for.
        pass

    return None


def _cpu_count() -> int:
    """The CPUs this process can actually use in parallel — not the host's
    total, and not only what a cpuset affinity mask reports.

    Two different things restrict a container, and neither implies the
    other:

      * ``--cpuset-cpus`` pins the process to specific cores. Linux's own
        affinity mask sees this; plain ``os.cpu_count()`` does not, which is
        why that alone is not used here.
      * ``--cpus`` / ``deploy.resources.limits.cpus`` caps total CPU *time*
        across however many cores the kernel happens to schedule onto. The
        affinity mask cannot see this either — under a `--cpus=2` limit on a
        24-core host, it still reports 24, and sizing the pool from that
        would spawn 24 workers to fight over a 2-core budget, each starved to
        a fraction of what a correctly-sized pool would have given it.

    The real ceiling is the smaller of whichever of the two actually apply.
    """
    try:
        cores = len(os.sched_getaffinity(0))
    except AttributeError:
        # sched_getaffinity doesn't exist off Linux (Windows, macOS); a dev
        # machine there has no cgroup to check either.
        return os.cpu_count() or 1

    quota = _cgroup_quota_cores()
    if quota is not None:
        # Floored, not rounded: a 2.5-core quota can usefully run 2 workers
        # at full tilt, not 3 fighting over the last half-core between them.
        cores = min(cores, max(1, int(quota)))

    return cores


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

    workers = min(max_workers or _cpu_count(), total)

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
    selected = [
        i
        for i in range(len(page.starts))
        if page.starts[i] < end and _word_end(page, i) > start
    ]
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


def highlight_metadata(page: PageWords, start: int, end: int) -> dict | None:
    """The full ``chunk_metadata["highlight"]`` value, or None if unavailable.

    ``v`` is a schema version: the one field that lets a future coordinate
    fix invalidate old rows without reading every one of them to check.
    """
    rects = rects_for_range(page, start, end)
    if not rects:
        return None

    return {"v": 1, "w": page.width, "h": page.height, "o": "tl", "r": rects}
