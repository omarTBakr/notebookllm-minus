"""qalam — reading-order Arabic text extraction, without OCR.

Same family as `text_layer.py`: it reads the PDF's own text rather than
re-rendering the page, so it is priced the same way — milliseconds, not
seconds. What makes it worth its own file rather than a fourth entry in
`text_layer.py` is that it is somebody else's answer to the exact problem
this package exists to measure: `pymupdf-raw` fuses words together,
`pymupdf-words` shatters them at every non-joining letter, and `pdfplumber`
gets whole lines reversed (see `arabic_extraction/README.md`). qalam's own pitch is fixing
precisely that, without paying tesseract's per-page cost.

It ships as Rust with published Python bindings — `pip install qalam`, no
build toolchain needed — and its API is a thin, already-parsed object rather
than a stream: `qalam.Document(path)` extracts every page up front and hands
back typed pages, confidence, and which pages it thinks need OCR instead.
That last part is a second opinion this package doesn't otherwise have: every
other text-layer extractor here just returns whatever it produced, right or
wrong, and `language.profile()` has to infer usability after the fact from
spacing. qalam says so directly, per page — `needs_ocr` and `reasons` — which
is worth reading even though the harness (`ArabicExtractor._extract` returns
only text) has nowhere to put it. See `QalamExtractor.inspect` for that
information outside the benchmark loop.

**Known issue (qalam 0.1.1, found on this corpus).** `.page(n).text` can come
back 200-300x too long on some documents — the real page content, correct,
with the document's own running header/footer repeated dozens to hundreds of
times — while `.confidence` still reports 1.0 for the affected page. Not
observed on every document (`ذخائر_لبنان.pdf` was clean throughout; see
`benchmark/reports/qalam/report/report.md`), so it is not filtered out here:
doing that silently would hide a real upstream bug behind extractor code
that looks like it works. **A relative guard does not work**: on the one
document this was measured against, 273 of 274 pages (99.6%) are affected,
so the document's own median page length is itself a bloated one and a
"many times the median" check flags zero of the corrupted pages — see the
report for the full census. A workable guard needs an absolute bound (or a
cross-check against a fast independent extractor such as `pymupdf-raw`),
which this project has not implemented yet — `OCR_EXTRACTOR=qalam` runs
without one.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ..base import ArabicExtractor, Page

if TYPE_CHECKING:  # pragma: no cover - typing only
    import qalam


class QalamExtractor(ArabicExtractor):
    """Wraps `qalam.Document` — parsed once per path and reused per page.

    `qalam.Document(path)` extracts the whole document eagerly on
    construction (its own docstring: "so every property below is a plain
    lookup that cannot fail"), while the harness asks one page at a time and
    usually asks for every page of the same file before moving to the next
    one. Reparsing the whole PDF on every page would multiply the cost by the
    page count for no reason, so the parsed document is cached by path.

    The production caller (`ProcessService._reread_unusable_arabic`) is
    not the harness: it submits every candidate page of one document to a
    thread pool at once, so the first access to a not-yet-cached path is a
    race between however many threads that document has candidates for. Each
    would see the cache empty and start its own `qalam.Document(path)` --
    every one of them a multi-second-to-minutes eager parse -- and only the
    last write would survive. `_lock` serialises the check-and-parse so the
    document is built once and every other thread waits for that result
    rather than duplicating it.
    """

    name = "qalam"
    description = "qalam — logical reading-order text-layer extraction, no OCR"
    reads_text_layer = True

    #: Path (as a string) -> parsed qalam.Document. Class-level, like the
    #: model caches on the OCR engines below, so every instance in one
    #: process shares the parse rather than repeating it.
    _documents: dict[str, "qalam.Document"] = {}
    _lock = threading.Lock()

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import qalam  # noqa: F401
        except ImportError:
            return False, "qalam is not installed (pip install qalam)"
        return True, ""

    @classmethod
    def _export_word_gap(cls) -> None:
        """Hand QALAM_WORD_GAP to qalam, which reads it from the environment.

        qalam decides a word break when the pen moves further than this
        fraction of the type size. Upstream hard-codes 0.25; the build this
        project installs (0.1.1+wordgap) reads `QALAM_WORD_GAP` instead, and
        on `ذخائر_لبنان.pdf` 0.25 fused whole lines into one "word" while 0.15
        spaced them correctly with no word split in two.

        Same shape as `TesseractBestExtractor._tessdata_dir`: the environment
        wins, so the package still runs standalone, then the application's
        setting — which pydantic reads from .env but never exports, so without
        this qalam would see nothing. qalam caches the value on first use, so
        this must run before the first `qalam.Document` in the process.
        """
        import os

        if os.environ.get("QALAM_WORD_GAP"):
            return

        try:
            from shared.utils import get_settings

            gap = getattr(get_settings(), "QALAM_WORD_GAP", None)
        except Exception:  # noqa: BLE001 - standalone use is expected
            return

        if gap:
            os.environ["QALAM_WORD_GAP"] = str(gap)

    @classmethod
    def _document(cls, path) -> "qalam.Document":
        import qalam

        cls._export_word_gap()

        key = str(path)

        # Fast path: no lock once the document is cached -- which is every
        # call but the first, given a document's pages are read together.
        if key in cls._documents:
            return cls._documents[key]

        with cls._lock:
            # Re-check: another thread may have parsed it while this one
            # waited for the lock.
            if key not in cls._documents:
                cls._documents[key] = qalam.Document(key)

            return cls._documents[key]

    def _extract(self, page: Page) -> str:
        # qalam counts pages from 1; the harness counts from 0, as pymupdf does.
        document = self._document(page.path)
        return document.page(page.number + 1).text

    @classmethod
    def pages_needing_ocr(cls, path, start: int, end: int) -> list[int]:
        """The pages in ``[start, end)`` qalam says have no usable text layer.

        Zero-based, like the pages they are compared with. qalam gives three
        reasons: the page paints no glyphs at all (a scan, or a picture of
        text), too few of its glyph codes resolve to characters, or glyphs were
        painted but produced almost no text.

        Asks about a *slice*, never the whole document, and deliberately skips
        the `_documents` cache. qalam parses eagerly, and on the known-bad
        document in the report a whole-document parse took 164s and 4.9 GB --
        the header-repetition bug again -- while the same 274 pages as 10-page
        slices took 2.9s in total at 164 MB, and flagged the same page. A parse
        worker only ever holds one batch anyway, and a cached whole document
        per asset would never be evicted in a long-lived worker.
        """
        import os
        import tempfile

        import pymupdf  # ty: ignore[unresolved-import]
        import qalam

        with pymupdf.open(str(path)) as source, pymupdf.open() as piece:
            piece.insert_pdf(source, from_page=start, to_page=end - 1)

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                sliced = tmp.name

            piece.save(sliced)

        try:
            cls._export_word_gap()
            document = qalam.Document(sliced)

            # qalam counts from 1 within the slice.
            return [start + number - 1 for number in document.pages_needing_ocr]
        finally:
            os.unlink(sliced)

    @classmethod
    def inspect(cls, path) -> dict:
        """qalam's own verdict for a document: confidence, and which pages it
        would send to OCR.

        Not part of the `ArabicExtractor` contract — the harness only ever
        asks for text — but worth surfacing separately, since qalam is the
        one extractor here that says *which* pages it does not trust rather
        than leaving that to `language.profile()` to guess.
        """
        document = cls._document(path)

        return {
            "confidence": document.confidence,
            "pages_needing_ocr": document.pages_needing_ocr,
            "pages": len(document),
        }
